"""Common interface every weather source implements. A source only needs to
answer two questions — "which forecast step covers this time?" and "give me
the grid for this tile at that step" — and the base class handles grouping
points by (tile, step), caching, and per-point nearest-neighbor extraction
identically for all of them.

All sources expose lat/lon on a -180..180 / -90..90 grid regardless of their
native convention (e.g. GFS's 0..360 longitude), so callers never deal with
per-source coordinate conventions.
"""

from __future__ import annotations

import hashlib
import threading
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import xarray as xr

from ..cache import DiskCache, tile_bbox, tile_index

# eccodes' GRIB definitions parser is NOT thread-safe — two threads decoding
# GRIB for the first time concurrently corrupted its flex scanner ("fatal
# flex scanner internal error", reproduced directly when tile fetches were
# first parallelized). Downloads can overlap freely; every cfgrib/eccodes
# DECODE must hold this lock. Decode itself is ~0.1-0.3s per tile, so
# serializing it costs little next to the multi-second network fetches.
GRIB_DECODE_LOCK = threading.Lock()


@dataclass(frozen=True)
class ForecastStep:
    """Identifies one fetchable grid snapshot for a source.

    key must be a filesystem-safe string, unique per (model run, forecast
    step) — it's used directly as the cache filename prefix.
    """

    key: str
    valid_time: datetime


@dataclass(frozen=True)
class BBox:
    west: float
    south: float
    east: float
    north: float


class WeatherSource(ABC):
    name: str
    variables: dict[str, str]  # canonical name -> this source's dataset variable name
    requires_auth: bool = False
    cache_max_age_hours: float = 48.0
    tile_deg: float = 10.0
    # Concurrent tile fetches per sample() call. Sources whose backend
    # rate-limits concurrent connections (ECMWF Open Data returns 429 with a
    # 120s retry — far worse than just fetching sequentially) set this to 1.
    max_parallel_fetches: int = 3

    def __init__(self):
        self._cache = DiskCache(self.name, max_age_hours=self.cache_max_age_hours)

    @abstractmethod
    def resolve_step(self, valid_time: datetime) -> ForecastStep:
        """Snap a requested valid time to the nearest step this source can serve."""

    @abstractmethod
    def fetch_tile(self, bbox: BBox, step: ForecastStep) -> xr.Dataset:
        """Download + decode (uncached) the grid for one tile at one step.
        Must return a Dataset with `latitude`/`longitude` coords in
        -180..180 / -90..90, sorted ascending."""

    def _get_tile(self, tile_i: int, tile_j: int, step: ForecastStep) -> xr.Dataset:
        # Include a fingerprint of the requested variable set in the cache
        # key — otherwise adding/removing a variable from `self.variables`
        # (e.g. wiring up wave period/direction after wave height was
        # already cached) silently serves the old, incomplete cached tile
        # instead of refetching, and the missing field just reads as NaN
        # with no error at all. Reproduced directly during development.
        var_fingerprint = hashlib.sha1("|".join(sorted(self.variables.values())).encode()).hexdigest()[:8]
        key = f"{step.key}_{tile_i}_{tile_j}_{var_fingerprint}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        bbox = BBox(*tile_bbox(tile_i, tile_j, self.tile_deg))
        ds = self.fetch_tile(bbox, step)
        self._cache.put(key, ds)
        return ds

    def sample(self, points: list[tuple[float, float, datetime]]) -> pd.DataFrame:
        """points: (lon, lat, valid_time) triples. Returns one row per point,
        same order, columns = ["lon", "lat", "time"] + canonical variable
        names (NaN where the source has no data there, e.g. on land)."""
        rows: list[dict] = [None] * len(points)  # type: ignore[list-item]
        step_by_time: dict[datetime, ForecastStep] = {}
        groups: dict[tuple, list[int]] = {}

        for idx, (lon, lat, t) in enumerate(points):
            if t not in step_by_time:
                step_by_time[t] = self.resolve_step(t)
            ti, tj = tile_index(lon, lat, self.tile_deg)
            groups.setdefault((step_by_time[t].key, ti, tj), []).append(idx)

        # Fetch all needed tiles first, concurrently — a multi-day route
        # spans many distinct forecast steps (a 9-day voyage is ~12 tile
        # fetches at 18h sampling), and they were previously downloaded one
        # at a time. Worker count is kept modest to stay polite to NOMADS.
        def _fetch(group_key):
            _, ti_, tj_ = group_key
            step_ = step_by_time[points[groups[group_key][0]][2]]
            try:
                return group_key, self._get_tile(ti_, tj_, step_)
            except Exception:
                # A failure fetching THIS tile/step must only NaN the points
                # that needed it, not the whole sample() call — a transient
                # blip on one waypoint's tile shouldn't discard every other
                # tile this source already fetched successfully.
                return group_key, None

        tiles: dict[tuple, xr.Dataset | None] = {}
        group_keys = list(groups)
        workers = min(self.max_parallel_fetches, len(group_keys))
        if workers <= 1:
            for k in group_keys:
                tiles[k] = _fetch(k)[1]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for k, ds in pool.map(_fetch, group_keys):
                    tiles[k] = ds

        for group_key, idxs in groups.items():
            ds = tiles[group_key]
            for idx in idxs:
                lon, lat, t = points[idx]
                row = {"lon": lon, "lat": lat, "time": t}
                try:
                    if ds is None:
                        raise RuntimeError("tile fetch failed")
                    pt = ds.sel(longitude=lon, latitude=lat, method="nearest")
                    for canon, var in self.variables.items():
                        row[canon] = float(pt[var].values) if var in pt else float("nan")
                except Exception:
                    for canon in self.variables:
                        row[canon] = float("nan")
                rows[idx] = row

        return pd.DataFrame(rows)
