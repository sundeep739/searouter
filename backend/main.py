"""SeaRouter API — FastAPI wrapper around the sea-distance engine (core.py).

All endpoints except /health require a Supabase JWT. The weather/ package is
present but dormant until Phase 2.
"""

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
