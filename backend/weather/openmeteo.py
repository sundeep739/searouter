"""Lightweight weather sampling using Open-Meteo's free JSON APIs (no key,
no GRIB/xarray/cfgrib) — chosen so Phase 2 fits a small/free backend.

Produces exactly the column names weather.speed_optimizer.build_segments
consumes (the `gfs_*` / `copernicus_*` namespaces are kept so the physics
and optimizer are reused unchanged), sourced from:
  - forecast API: 10 m wind (speed + direction)  -> wind u/v
  - marine API:   waves (height, period, direction) + surface currents

Conventions (see weather/resistance.py):
  - wind u/v are "blowing toward" components; Open-Meteo wind_direction is the
    direction wind comes FROM, so u=-V·sin, v=-V·cos.
  - current u/v are the flow ("toward") components; Open-Meteo current
    direction is the direction the current flows toward, so u=V·sin, v=V·cos.
"""

import logging
import math
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd

from core import NAUTICAL_MILES_PER_KM, _haversine_km

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
NAN = float("nan")


def subsample(coords, speed_knots, departure, interval_hours=12.0, max_points=24):
    """Reduce a dense route to points ~interval_hours of sailing apart (always
    including first and last), each tagged with its ETA (UTC). Returns a list
    of (lon, lat, eta)."""
    if len(coords) < 2 or speed_knots <= 0:
        return []
    cum = [0.0]
    for a, b in zip(coords, coords[1:]):
        cum.append(cum[-1] + _haversine_km(a, b) * NAUTICAL_MILES_PER_KM)
    total_nm = cum[-1]
    if total_nm <= 0:
        return []
    step_nm = max(interval_hours * speed_knots, 1.0)
    n = min(max_points, max(2, int(total_nm / step_nm) + 1))
    if departure.tzinfo is None:
        departure = departure.replace(tzinfo=timezone.utc)

    pts, j = [], 0
    for i in range(n):
        target = total_nm * i / (n - 1)
        # advance to the last node at or before the target distance
        while j < len(cum) - 1 and cum[j + 1] <= target:
            j += 1
        lon, lat = coords[min(j, len(coords) - 1)]
        pts.append((lon, lat, departure + timedelta(hours=target / speed_knots)))
    return pts


def _fetch(url, pts, hourly_vars, extra):
    params = {
        "latitude": ",".join(f"{p[1]:.4f}" for p in pts),
        "longitude": ",".join(f"{p[0]:.4f}" for p in pts),
        "hourly": ",".join(hourly_vars),
        "timezone": "GMT",
        "forecast_days": 16,
        **extra,
    }
    resp = httpx.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else [data]


def _at(loc, eta, var):
    """Hourly value for `var` at the point's ETA hour, or NaN if unavailable
    (e.g. ETA beyond the ~16-day forecast horizon, or a land/null cell)."""
    if not loc:
        return NAN
    hourly = loc.get("hourly") or {}
    times = hourly.get("time")
    vals = hourly.get(var)
    if not times or not vals:
        return NAN
    if eta.tzinfo is None:
        eta = eta.replace(tzinfo=timezone.utc)
    key = eta.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00")
    try:
        idx = times.index(key)
    except ValueError:
        return NAN
    v = vals[idx] if idx < len(vals) else None
    return float(v) if v is not None else NAN


def _wind_uv(speed_ms, from_deg):
    if not (math.isfinite(speed_ms) and math.isfinite(from_deg)):
        return NAN, NAN
    r = math.radians(from_deg)
    return -speed_ms * math.sin(r), -speed_ms * math.cos(r)


def _current_uv(vel_kmh, toward_deg):
    if not (math.isfinite(vel_kmh) and math.isfinite(toward_deg)):
        return NAN, NAN
    vel_ms = vel_kmh / 3.6
    r = math.radians(toward_deg)
    return vel_ms * math.sin(r), vel_ms * math.cos(r)


def sample_route(coords, speed_knots, departure, interval_hours=12.0):
    """Sample wind/waves/currents along a route. Returns (sub_coords, df)
    where df has one row per sub-coord with the columns build_segments reads,
    plus lon/lat/eta and display-friendly wind_speed_ms/current_speed_ms.
    Never raises on network failure — missing data becomes NaN (treated as
    calm/conservative by the optimizer)."""
    pts = subsample(coords, speed_knots, departure, interval_hours)
    if not pts:
        return [], pd.DataFrame()

    try:
        wind = _fetch(FORECAST_URL, pts, ["wind_speed_10m", "wind_direction_10m"], {"windspeed_unit": "ms"})
    except Exception as exc:  # noqa: BLE001 - best-effort weather
        log.warning("open-meteo wind fetch failed: %s", exc)
        wind = []
    try:
        marine = _fetch(
            MARINE_URL,
            pts,
            ["wave_height", "wave_direction", "wave_period", "ocean_current_velocity", "ocean_current_direction"],
            {},
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("open-meteo marine fetch failed: %s", exc)
        marine = []

    rows = []
    for i, (lon, lat, eta) in enumerate(pts):
        wloc = wind[i] if i < len(wind) else None
        mloc = marine[i] if i < len(marine) else None
        wind_speed = _at(wloc, eta, "wind_speed_10m")
        wu, wv = _wind_uv(wind_speed, _at(wloc, eta, "wind_direction_10m"))
        cur_kmh = _at(mloc, eta, "ocean_current_velocity")
        cu, cv = _current_uv(cur_kmh, _at(mloc, eta, "ocean_current_direction"))
        rows.append(
            {
                "lon": lon,
                "lat": lat,
                "eta": eta.astimezone(timezone.utc).isoformat(),
                "gfs_wind__wind_u_ms": wu,
                "gfs_wind__wind_v_ms": wv,
                "wind_speed_ms": wind_speed,
                "gfs_wave__sig_wave_height_m": _at(mloc, eta, "wave_height"),
                "gfs_wave__wave_period_s": _at(mloc, eta, "wave_period"),
                "gfs_wave__wave_from_direction_deg": _at(mloc, eta, "wave_direction"),
                "copernicus_current__current_u_ms": cu,
                "copernicus_current__current_v_ms": cv,
                "current_speed_ms": NAN if not math.isfinite(cur_kmh) else cur_kmh / 3.6,
            }
        )

    sub_coords = [(lon, lat) for lon, lat, _ in pts]
    return sub_coords, pd.DataFrame(rows)
