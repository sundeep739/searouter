"""ECMWF Open Data (IFS HRES) — 10m wind, as an independent/ensemble check
against NOAA GFS. Free, no auth. Unlike NOMADS, there's no server-side bbox
subsetting: each (step) pull is the whole global 0.25deg grid (~1.5-2MB per
param). We download it once per step and keep it in memory for the rest of
the process, so N tiles at the same step cost one network call, not N — the
tile-keyed disk cache (from the shared base class) then also means a second
process run skips the network entirely for a tile it already has."""

import os
import tempfile
import time
from datetime import datetime, timedelta, timezone

import xarray as xr
from ecmwf.opendata import Client

from .base import GRIB_DECODE_LOCK, BBox, ForecastStep, WeatherSource

VALID_STEPS_3H = list(range(0, 145, 3))     # 0..144h, 3-hourly
VALID_STEPS_6H = list(range(150, 241, 6))   # 144..240h, 6-hourly
VALID_STEPS = VALID_STEPS_3H + VALID_STEPS_6H
_RUN_CACHE_TTL_S = 3600


def _nearest_valid_step(hours_ahead: float) -> int:
    hours_ahead = max(0, hours_ahead)
    return min(VALID_STEPS, key=lambda s: abs(s - hours_ahead))


class ECMWFWindSource(WeatherSource):
    name = "ecmwf_wind"
    variables = {"wind_u_ms": "u10", "wind_v_ms": "v10"}
    requires_auth = False
    cache_max_age_hours = 6.0
    # ECMWF's Open Data portal 429s concurrent requests, and its client
    # retries with a 120s backoff — far slower than just fetching each
    # step's grid one at a time (reproduced directly under 3-way fetches).
    max_parallel_fetches = 1

    def __init__(self):
        super().__init__()
        self._client = Client(source="ecmwf")
        self._run_cache: tuple[datetime, float] | None = None
        self._global_grid_cache: dict[str, xr.Dataset] = {}

    def _latest_run(self) -> datetime:
        if self._run_cache and time.time() - self._run_cache[1] < _RUN_CACHE_TTL_S:
            return self._run_cache[0]
        run_time = self._client.latest(type="fc")
        run_time = run_time.replace(tzinfo=timezone.utc)
        self._run_cache = (run_time, time.time())
        return run_time

    def resolve_step(self, valid_time: datetime) -> ForecastStep:
        run_start = self._latest_run()
        if valid_time.tzinfo is None:
            valid_time = valid_time.replace(tzinfo=timezone.utc)
        hours_ahead = (valid_time - run_start).total_seconds() / 3600
        step = _nearest_valid_step(hours_ahead)
        return ForecastStep(
            key=f"ecmwf_{run_start:%Y%m%d%H}_f{step:03d}",
            valid_time=run_start + timedelta(hours=step),
        )

    def _global_grid(self, step_key: str) -> xr.Dataset:
        if step_key in self._global_grid_cache:
            return self._global_grid_cache[step_key]

        _, run_str, fpart = step_key.split("_")
        step = int(fpart[1:])

        fd, tmp_path = tempfile.mkstemp(suffix=".grib2")
        os.close(fd)
        try:
            self._client.retrieve(type="fc", step=step, param=["10u", "10v"], target=tmp_path)
            with GRIB_DECODE_LOCK:  # eccodes decode is not thread-safe
                ds = xr.open_dataset(tmp_path, engine="cfgrib").load()
        finally:
            os.remove(tmp_path)

        ds = ds.assign_coords(longitude=(((ds.longitude + 180) % 360) - 180)).sortby("longitude")
        self._global_grid_cache[step_key] = ds
        return ds

    def fetch_tile(self, bbox: BBox, step: ForecastStep) -> xr.Dataset:
        grid = self._global_grid(step.key)
        return grid.sel(
            longitude=slice(bbox.west, bbox.east),
            latitude=slice(bbox.north, bbox.south),  # descending lat order
        )
