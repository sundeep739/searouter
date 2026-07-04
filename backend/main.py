"""SeaRouter API — FastAPI wrapper around the sea-distance engine (core.py).

All endpoints except /health require a Supabase JWT. Weather-aware planning
(/weather/plan) reuses the weather/ physics with a light Open-Meteo data layer;
its heavy deps (scipy) are imported lazily so base memory stays low.
"""

import math
import os
from datetime import datetime, timedelta

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field
from searoute.classes.passages import Passage

import core
from auth import SERVICE_ROLE_KEY, SUPABASE_URL, require_admin, verify_token

VALID_PASSAGES = sorted(core.PASSAGE_LABELS)

app = FastAPI(title="SeaRouter API", version="1.0")
app.add_middleware(GZipMiddleware, minimum_size=1024)

_origins = [
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", "").split(",")
    if o.strip()
] or ["http://localhost:5173", "http://127.0.0.1:5173"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def warm():
    core.load_locations()


def _restrictions(avoid: list[str]) -> tuple:
    invalid = [p for p in avoid if p not in core.PASSAGE_LABELS]
    if invalid:
        raise HTTPException(
            400, f"Unknown passage(s) in avoid: {invalid}. Valid: {VALID_PASSAGES}"
        )
    return tuple(sorted(set(avoid) | {Passage.northwest}))


def _resolve(query: str, what: str):
    row, how = core.resolve(query)
    if row is None:
        raise HTTPException(404, f"Could not resolve {what}: {query!r}")
    return row, how


def _loc(row, how=None):
    d = {
        "unlocode": row["unlocode"],
        "name": row["name"],
        "country": row["country"],
        "lat": round(float(row["lat"]), 5),
        "lon": round(float(row["lon"]), 5),
        "is_seaport": bool(row["is_seaport"]),
    }
    if how:
        d["resolved_by"] = how
    return d


def _round_coords(coords):
    return [[round(c[0], 4), round(c[1], 4)] for c in coords]


def _variant_out(v, speed):
    nm = v["length_km"] * core.NAUTICAL_MILES_PER_KM
    hours = nm / speed if speed else None
    return {
        "name": v["name"],
        "avoided": v.get("avoided"),
        "distance_km": round(v["length_km"], 1),
        "distance_nm": round(nm, 1),
        "hours": round(hours, 1) if hours else None,
        "passages": v["passages"],
        "passage_names": core.passage_names(v["passages"]),
        "coords": _round_coords(v["coords"]),
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/search")
def search(
    q: str = Query(..., min_length=1),
    limit: int = Query(8, ge=1, le=25),
    _=Depends(verify_token),
):
    df = core.load_locations()
    matches = core.search(df, q, limit=limit)
    return {"results": [_loc(row) for _, row in matches.iterrows()]}


class Schedule(BaseModel):
    etd: datetime
    eta: datetime


class RouteReq(BaseModel):
    origin: str
    dest: str
    waypoints: list[list[float]] = []  # [[lon, lat], ...]
    avoid: list[str] = []
    speed: float = Field(24, ge=1, le=60)
    schedule: Schedule | None = None


@app.post("/route")
def route(req: RouteReq, _=Depends(verify_token)):
    restrictions = _restrictions(req.avoid)
    o_row, o_how = _resolve(req.origin, "origin")
    d_row, d_how = _resolve(req.dest, "dest")
    o_pt, d_pt = (o_row["lon"], o_row["lat"]), (d_row["lon"], d_row["lat"])

    variants = core.route_variants(o_pt, d_pt, restrictions)
    if not variants:
        raise HTTPException(422, "No sea route exists between these locations")

    speed = req.speed
    resp: dict = {
        "origin": _loc(o_row, o_how),
        "dest": _loc(d_row, d_how),
        "avoid": req.avoid,
        "speed_knots": speed,
        "variants": [_variant_out(v, speed) for v in variants],
    }

    if req.waypoints:
        wps = [tuple(w) for w in req.waypoints]
        sequence = core.order_waypoints(o_pt, d_pt, wps)
        legs, gap = core.compute_legs(sequence, restrictions)
        if gap is not None:
            raise HTTPException(
                422, f"No sea route between waypoints near {gap[0]} and {gap[1]}"
            )
        total_km = sum(leg["length_km"] for leg in legs)
        nm = total_km * core.NAUTICAL_MILES_PER_KM
        coords: list = []
        for leg in legs:
            coords.extend(leg["coords"])
        resp["custom"] = {
            "waypoint_order": [[round(p[0], 5), round(p[1], 5)] for p in sequence],
            "distance_km": round(total_km, 1),
            "distance_nm": round(nm, 1),
            "hours": round(nm / speed, 1),
            "coords": _round_coords(coords),
        }

    if req.schedule:
        target_h = (req.schedule.eta - req.schedule.etd).total_seconds() / 3600
        if target_h <= 0:
            raise HTTPException(400, "schedule.eta must be after schedule.etd")
        headline_nm = (
            resp["custom"]["distance_nm"]
            if "custom" in resp
            else resp["variants"][0]["distance_nm"]
        )
        resp["schedule"] = {
            "target_hours": round(target_h, 1),
            "required_speed_knots": round(headline_nm / target_h, 2),
        }
    return resp


class VoyageReq(BaseModel):
    ports: list[str] = Field(..., min_length=2)
    avoid: list[str] = []
    speed: float = Field(24, ge=1, le=60)
    dwell_hours: float = Field(0, ge=0)
    departure: datetime | None = None


@app.post("/voyage")
def voyage(req: VoyageReq, _=Depends(verify_token)):
    restrictions = _restrictions(req.avoid)
    rows = [_resolve(p, f"port {i + 1}")[0] for i, p in enumerate(req.ports)]
    legs_out = []
    total_nm = total_hours = 0.0
    clock = req.departure
    for a, b in zip(rows, rows[1:]):
        leg = core.compute_route(a["lon"], a["lat"], b["lon"], b["lat"], restrictions)
        if not leg["coords"] or not leg["length_km"]:
            raise HTTPException(
                422, f"No sea route between {a['unlocode']} and {b['unlocode']}"
            )
        nm = leg["length_km"] * core.NAUTICAL_MILES_PER_KM
        hours = nm / req.speed
        out = {
            "from": _loc(a),
            "to": _loc(b),
            "distance_km": round(leg["length_km"], 1),
            "distance_nm": round(nm, 1),
            "hours": round(hours, 1),
            "passages": core.passage_names(leg["passages"]),
            "coords": _round_coords(leg["coords"]),
        }
        if clock:
            out["etd"] = clock.isoformat()
            clock += timedelta(hours=hours)
            out["eta"] = clock.isoformat()
            clock += timedelta(hours=req.dwell_hours)
        total_nm += nm
        total_hours += hours
        legs_out.append(out)
    dwell_total = req.dwell_hours * max(len(rows) - 2, 0)
    return {
        "legs": legs_out,
        "total_nm": round(total_nm, 1),
        "sea_hours": round(total_hours, 1),
        "total_hours": round(total_hours + dwell_total, 1),
    }


class MatrixReq(BaseModel):
    origins: list[str] = Field(..., min_length=1)
    dests: list[str] = Field(..., min_length=1)
    avoid: list[str] = []
    speed: float = Field(24, ge=1, le=60)


@app.post("/matrix")
def matrix(req: MatrixReq, _=Depends(verify_token)):
    restrictions = _restrictions(req.avoid)
    o_rows = [_resolve(p, f"origin {p!r}")[0] for p in req.origins]
    d_rows = [_resolve(p, f"dest {p!r}")[0] for p in req.dests]
    cells = []
    for o in o_rows:
        row_cells = []
        for d in d_rows:
            if o["unlocode"] == d["unlocode"]:
                row_cells.append({"distance_nm": 0, "hours": 0})
                continue
            r = core.compute_route(o["lon"], o["lat"], d["lon"], d["lat"], restrictions)
            if not r["coords"] or not r["length_km"]:
                row_cells.append(None)
                continue
            nm = r["length_km"] * core.NAUTICAL_MILES_PER_KM
            row_cells.append(
                {"distance_nm": round(nm, 1), "hours": round(nm / req.speed, 1)}
            )
        cells.append(row_cells)
    return {
        "origins": [_loc(o) for o in o_rows],
        "dests": [_loc(d) for d in d_rows],
        "cells": cells,
    }


class VerifyReq(BaseModel):
    origin: str
    dest: str
    reported_nm: float = Field(..., gt=0)
    tolerance_pct: float = Field(5, gt=0, le=100)
    avoid: list[str] = []


@app.post("/verify")
def verify(req: VerifyReq, _=Depends(verify_token)):
    restrictions = _restrictions(req.avoid)
    o_row, _how = _resolve(req.origin, "origin")
    d_row, _how2 = _resolve(req.dest, "dest")
    variants = core.route_variants(
        (o_row["lon"], o_row["lat"]), (d_row["lon"], d_row["lat"]), restrictions
    )
    if not variants:
        raise HTTPException(422, "No sea route exists between these locations")
    result = core.assess_reported(variants, req.reported_nm, req.tolerance_pct)
    return {
        "origin": _loc(o_row),
        "dest": _loc(d_row),
        "reported_nm": req.reported_nm,
        "tolerance_pct": req.tolerance_pct,
        **result,
    }


class BatchRow(BaseModel):
    origin: str
    dest: str
    reported_nm: float


class BatchReq(BaseModel):
    rows: list[BatchRow] = Field(..., min_length=1, max_length=500)
    tolerance_pct: float = Field(5, gt=0, le=100)
    avoid: list[str] = []


@app.post("/verify/batch")
def verify_batch(req: BatchReq, _=Depends(verify_token)):
    restrictions = _restrictions(req.avoid)
    results = []
    for row in req.rows:
        try:
            o_row, _h = _resolve(row.origin, "origin")
            d_row, _h2 = _resolve(row.dest, "dest")
            variants = core.route_variants(
                (o_row["lon"], o_row["lat"]),
                (d_row["lon"], d_row["lat"]),
                restrictions,
            )
            if not variants:
                raise HTTPException(422, "No sea route")
            assessed = core.assess_reported(variants, row.reported_nm, req.tolerance_pct)
            results.append(
                {
                    "origin": o_row["unlocode"],
                    "dest": d_row["unlocode"],
                    "reported_nm": row.reported_nm,
                    "verdict": assessed["verdict"],
                    "best_match": assessed["best_match"],
                }
            )
        except HTTPException as exc:
            results.append(
                {
                    "origin": row.origin,
                    "dest": row.dest,
                    "reported_nm": row.reported_nm,
                    "verdict": "error",
                    "error": exc.detail,
                }
            )
    return {"results": results, "tolerance_pct": req.tolerance_pct}


HAZARD_WAVE_M = 4.0
HAZARD_WIND_MS = 17.0


def _num(x, ndigits=2):
    """JSON-safe number: NaN/inf -> None (invalid JSON otherwise)."""
    if x is None:
        return None
    x = float(x)
    return None if not math.isfinite(x) else round(x, ndigits)


@app.get("/vessels/presets")
def vessel_presets(_=Depends(verify_token)):
    from weather.fleet import vessel_to_dict
    from weather.vessel import PRESETS

    return {"presets": {name: vessel_to_dict(v) for name, v in PRESETS.items()}}


class WeatherPlanReq(BaseModel):
    origin: str
    dest: str
    avoid: list[str] = []
    waypoints: list[list[float]] = []
    speed: float | None = Field(None, ge=1, le=60)
    schedule: Schedule | None = None
    departure: datetime | None = None
    vessel: dict
    loading: str = Field("laden", pattern="^(laden|ballast)$")
    objective: str = Field("steady_power", pattern="^(steady_power|min_fuel)$")
    interval_hours: float = Field(12, ge=3, le=24)


@app.post("/weather/plan")
def weather_plan(req: WeatherPlanReq, _=Depends(verify_token)):
    # Heavy weather deps loaded only when this endpoint is hit.
    from weather.fleet import vessel_from_dict
    from weather.openmeteo import sample_route
    from weather.speed_optimizer import MS_PER_KNOT, build_segments, smooth_speed

    restrictions = _restrictions(req.avoid)
    o_row, _h = _resolve(req.origin, "origin")
    d_row, _h2 = _resolve(req.dest, "dest")
    o_pt, d_pt = (o_row["lon"], o_row["lat"]), (d_row["lon"], d_row["lat"])

    if req.waypoints:
        seq = core.order_waypoints(o_pt, d_pt, [tuple(w) for w in req.waypoints])
        legs, gap = core.compute_legs(seq, restrictions)
        if gap is not None:
            raise HTTPException(422, "No sea route through those waypoints")
        coords: list = []
        for leg in legs:
            coords.extend(leg["coords"])
    else:
        variants = core.route_variants(o_pt, d_pt, restrictions)
        if not variants:
            raise HTTPException(422, "No sea route exists between these locations")
        coords = variants[0]["coords"]
    coords = [(float(c[0]), float(c[1])) for c in coords]

    route_nm = sum(
        core._haversine_km(coords[i], coords[i + 1]) for i in range(len(coords) - 1)
    ) * core.NAUTICAL_MILES_PER_KM

    try:
        vessel = vessel_from_dict(req.vessel)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Invalid vessel spec: {exc}")

    nominal_speed = target_hours = None
    if req.schedule:
        target_hours = (req.schedule.eta - req.schedule.etd).total_seconds() / 3600
        if target_hours <= 0:
            raise HTTPException(400, "schedule.eta must be after schedule.etd")
        departure = req.schedule.etd
        sampling_speed = route_nm / target_hours
    elif req.speed:
        nominal_speed = req.speed
        departure = req.departure or datetime.utcnow()
        sampling_speed = req.speed
    else:
        raise HTTPException(400, "Provide either speed or schedule")

    sub_coords, wx = sample_route(coords, sampling_speed, departure, req.interval_hours)
    if len(sub_coords) < 2:
        raise HTTPException(422, "Route too short to build a weather plan")

    segments = build_segments(sub_coords, wx)
    plan = smooth_speed(
        sub_coords,
        wx,
        vessel,
        nominal_speed_knots=nominal_speed,
        target_hours=target_hours,
        loading=req.loading,
        objective=req.objective,
    )

    speeds = plan["segment_speeds_knots"]
    powers = plan["segment_power_kw"]
    fuels = plan["segment_fuel_t"]
    dists = plan["segment_distances_nm"]

    legs_out, max_wave, max_wind, hazard_count = [], 0.0, 0.0, 0
    for i, seg in enumerate(segments):
        row = wx.iloc[i]
        wave = row["gfs_wave__sig_wave_height_m"]
        wind = row["wind_speed_ms"]
        hazard = (math.isfinite(wave) and wave > HAZARD_WAVE_M) or (
            math.isfinite(wind) and wind > HAZARD_WIND_MS
        )
        if hazard:
            hazard_count += 1
        if math.isfinite(wave):
            max_wave = max(max_wave, wave)
        if math.isfinite(wind):
            max_wind = max(max_wind, wind)
        stw = speeds[i]
        current_assist = seg["current_assist_kn"]
        legs_out.append(
            {
                "from": [round(sub_coords[i][0], 4), round(sub_coords[i][1], 4)],
                "to": [round(sub_coords[i + 1][0], 4), round(sub_coords[i + 1][1], 4)],
                "eta": row["eta"],
                "distance_nm": _num(dists[i], 1),
                "stw_knots": _num(stw, 2),
                "current_assist_kn": _num(current_assist, 2),
                "sog_knots": _num(stw + current_assist, 2),
                "power_kw": _num(powers[i], 0),
                "fuel_t": _num(fuels[i], 2),
                "wind_speed_ms": _num(wind, 1),
                "wave_height_m": _num(wave, 2),
                "current_speed_ms": _num(row["current_speed_ms"], 2),
                "hazard": bool(hazard),
            }
        )

    sampled = [
        {
            "lon": round(float(r["lon"]), 4),
            "lat": round(float(r["lat"]), 4),
            "eta": r["eta"],
            "wind_speed_ms": _num(r["wind_speed_ms"], 1),
            "wave_height_m": _num(r["gfs_wave__sig_wave_height_m"], 2),
            "current_speed_ms": _num(r["current_speed_ms"], 2),
        }
        for _, r in wx.iterrows()
    ]

    fb, fo = plan["fuel_baseline_t"], plan["fuel_optimized_t"]
    saved_pct = ((fb - fo) / fb * 100) if fb else 0.0
    return {
        "vessel": vessel.name,
        "loading": req.loading,
        "objective": req.objective,
        "route_nm": _num(route_nm, 1),
        "sampled_points": len(sampled),
        "plan": {
            "success": plan["success"],
            "message": plan["message"],
            "nominal_speed_knots": _num(plan["nominal_speed_knots"], 2),
            "baseline_speed_knots": _num(plan["baseline_speed_knots"], 2),
            "total_hours_optimized": _num(plan["total_hours_optimized"], 1),
            "total_hours_baseline": _num(plan["total_hours_baseline"], 1),
            "fuel_baseline_t": _num(fb, 2),
            "fuel_optimized_t": _num(fo, 2),
            "fuel_saved_pct": _num(saved_pct, 1),
            "power_std_baseline_kw": _num(plan["power_std_baseline"], 0),
            "power_std_optimized_kw": _num(plan["power_std_optimized"], 0),
            "sfoc_modeled": plan["sfoc_modeled"],
        },
        "hazards": {
            "count": hazard_count,
            "max_wave_m": _num(max_wave, 2),
            "max_wind_ms": _num(max_wind, 1),
            "wave_threshold_m": HAZARD_WAVE_M,
            "wind_threshold_ms": HAZARD_WIND_MS,
        },
        "legs": legs_out,
        "sampled": sampled,
    }


class InviteReq(BaseModel):
    email: str
    role: str = Field("member", pattern="^(admin|member)$")


@app.post("/admin/invite")
def admin_invite(req: InviteReq, _=Depends(require_admin)):
    if not SUPABASE_URL or not SERVICE_ROLE_KEY:
        raise HTTPException(500, "Supabase admin credentials not configured")
    headers = {
        "apikey": SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SERVICE_ROLE_KEY}",
    }
    resp = httpx.post(
        f"{SUPABASE_URL}/auth/v1/invite",
        json={"email": req.email},
        headers=headers,
        timeout=15,
    )
    if resp.status_code not in (200, 201):
        raise HTTPException(502, f"Invite failed: {resp.text}")
    user = resp.json()
    if req.role == "admin" and user.get("id"):
        httpx.patch(
            f"{SUPABASE_URL}/rest/v1/profiles",
            params={"id": f"eq.{user['id']}"},
            json={"role": "admin"},
            headers={**headers, "Prefer": "return=minimal"},
            timeout=15,
        )
    return {"invited": req.email, "role": req.role}
