"""Bridges a core.py route (a polyline of real sea-lane waypoints) to the
weather sources: compute an ETA at each waypoint from speed + departure time,
then batch-sample every source at those (lon, lat, time) points.

Each source's columns are namespaced by source name (e.g. gfs_wind__wind_u_ms
vs ecmwf_wind__wind_u_ms) since GFS and ECMWF both report the same physical
quantity (10m wind) under the same canonical names — keeping both lets the
hazard/ensemble-agreement check in weather/routing.py compare them directly.

Source instances are kept as module-level singletons so each source's
in-memory caches (e.g. ECMWF's per-step global grid, GFS's resolved run)
are reused across repeated calls within a process, on top of the on-disk
tile cache each source already has individually."""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pandas as pd

from core import NAUTICAL_MILES_PER_KM, _haversine_km
from .sources.base import WeatherSource
from .sources.copernicus import CopernicusCurrentSource
from .sources.ecmwf import ECMWFWindSource
from .sources.gfs_wave import GFSWaveSource
from .sources.gfs_wind import GFSWindSource

logger = logging.getLogger(__name__)

SOURCE_CLASSES = [GFSWindSource, GFSWaveSource, ECMWFWindSource, CopernicusCurrentSource]
_instances: dict[type, WeatherSource] = {}


def _get_source(cls: type) -> WeatherSource:
    if cls not in _instances:
        _instances[cls] = cls()
    return _instances[cls]


def subsample_waypoints(coords: list[tuple[float, float]], speed_knots: float,
                         interval_hours: float = 18.0) -> list[tuple[float, float]]:
    """Reduces a dense sea-lane polyline (core.py routes can have 90+ raw
    nodes) to roughly one point per interval_hours of transit — a realistic
    cadence for both weather sampling and the speed optimizer's segments
    (a captain doesn't re-check the forecast or retune engine speed every
    few km). Always keeps the first and last point exactly.

    Callers that also need hazard polygons or optimizer segments MUST reuse
    the coords list this returns (not the original route coords) for those
    — sample_along_route()'s output has one row per input point, so mixing
    subsampled weather with full-route coords elsewhere is a length
    mismatch. This is deliberately a separate, explicit step rather than
    something sample_along_route does silently, so that contract stays
    obvious at every call site."""
    if len(coords) <= 2:
        return list(coords)

    interval_nm = speed_knots * interval_hours
    kept = [coords[0]]
    cum_nm = 0.0
    last_kept_nm = 0.0
    for a, b in zip(coords, coords[1:]):
        cum_nm += _haversine_km(a, b) * NAUTICAL_MILES_PER_KM
        if cum_nm - last_kept_nm >= interval_nm:
            kept.append(b)
            last_kept_nm = cum_nm
    if kept[-1] != coords[-1]:
        kept.append(coords[-1])
    return kept


def eta_along_route(coords: list[tuple[float, float]], speed_knots: float,
                     departure: datetime | None = None) -> list[datetime]:
    """coords: (lon, lat) pairs, in route order. Returns one UTC datetime per
    coord — the estimated arrival time at that point, walking cumulative
    great-circle distance at speed_knots from departure (defaults to now)."""
    if departure is None:
        departure = datetime.now(timezone.utc)
    elif departure.tzinfo is None:
        departure = departure.replace(tzinfo=timezone.utc)

    etas = [departure]
    cum_km = 0.0
    for a, b in zip(coords, coords[1:]):
        cum_km += _haversine_km(a, b)
        hours = cum_km * NAUTICAL_MILES_PER_KM / speed_knots
        etas.append(departure + timedelta(hours=hours))
    return etas


def sample_along_route(coords: list[tuple[float, float]], speed_knots: float,
                        departure: datetime | None = None,
                        sources: list[type] | None = None) -> pd.DataFrame:
    """Returns one row per route waypoint: lon, lat, eta, plus every
    source's variables prefixed "{source_name}__". A source that fails
    (network error, or Copernicus without valid credentials) contributes
    NaN columns and a logged warning rather than aborting the whole sample —
    weather annotation should degrade gracefully, not block routing."""
    etas = eta_along_route(coords, speed_knots, departure)
    points = [(lon, lat, t) for (lon, lat), t in zip(coords, etas)]

    result = pd.DataFrame({
        "lon": [p[0] for p in points],
        "lat": [p[1] for p in points],
        "eta": etas,
    })

    # Sources are independent services (NOMADS, ECMWF, Copernicus) — fetch
    # them concurrently instead of one after another. Instances are created
    # up front on the main thread so the module-level singleton dict is
    # never mutated from worker threads.
    instances = [_get_source(cls) for cls in (sources or SOURCE_CLASSES)]

    def _run(src: WeatherSource):
        try:
            return src.sample(points)
        except Exception as e:
            logger.warning("%s sample failed: %s", src.name, e)
            return None

    if len(instances) == 1:
        frames = [_run(instances[0])]
    else:
        with ThreadPoolExecutor(max_workers=len(instances)) as pool:
            frames = list(pool.map(_run, instances))

    for src, df in zip(instances, frames):
        for canon in src.variables:
            col = f"{src.name}__{canon}"
            result[col] = df[canon].values if df is not None else float("nan")

    return result
