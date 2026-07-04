"""NOAA GFS atmosphere — 10m wind, via NOMADS's GRIB filter service.
Free, no auth. Server-side bbox subsetting keeps each tile fetch small
(~a few hundred KB), decoded with cfgrib."""

import os
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import xarray as xr

from .base import GRIB_DECODE_LOCK, BBox, ForecastStep, WeatherSource

STEP_HOURS = 3
MAX_FORECAST_HOURS = 384
_RUN_CACHE_TTL_S = 3600
_MAX_OLDER_RUN_FALLBACKS = 4  # up to 24h of older 6-hourly cycles


class GFSWindSource(WeatherSource):
    name = "gfs_wind"
    variables = {"wind_u_ms": "u10", "wind_v_ms": "v10"}
    requires_auth = False
    cache_max_age_hours = 6.0  # GFS reissues every 6h

    def __init__(self):
        super().__init__()
        self._run_cache: tuple[str, str, float] | None = None

    def _latest_run(self) -> tuple[str, str]:
        """Probe NOMADS for the most recently published run (date, hour)."""
        if self._run_cache and time.time() - self._run_cache[2] < _RUN_CACHE_TTL_S:
            return self._run_cache[0], self._run_cache[1]
        now = datetime.now(timezone.utc)
        for days_back in (0, 1):
            day = (now - timedelta(days=days_back)).strftime("%Y%m%d")
            for hour in ("18", "12", "06", "00"):
                url = (
                    "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/"
                    f"gfs.{day}/{hour}/atmos/gfs.t{hour}z.pgrb2.0p25.f000"
                )
                try:
                    urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=10)
                    self._run_cache = (day, hour, time.time())
                    return day, hour
                except Exception:
                    continue
        raise RuntimeError("No recent GFS run found on NOMADS")

    def resolve_step(self, valid_time: datetime) -> ForecastStep:
        day, hour = self._latest_run()
        run_start = datetime.strptime(f"{day}{hour}", "%Y%m%d%H").replace(tzinfo=timezone.utc)
        if valid_time.tzinfo is None:
            valid_time = valid_time.replace(tzinfo=timezone.utc)
        hours_ahead = (valid_time - run_start).total_seconds() / 3600
        step = int(round(hours_ahead / STEP_HOURS) * STEP_HOURS)
        step = max(0, min(step, MAX_FORECAST_HOURS))
        return ForecastStep(
            key=f"gfswind_{day}_{hour}z_f{step:03d}",
            valid_time=run_start + timedelta(hours=step),
        )

    def fetch_tile(self, bbox: BBox, step: ForecastStep) -> xr.Dataset:
        _, day, hour_z, fpart = step.key.split("_")
        hour = hour_z[:-1]
        fstep = int(fpart[1:])
        run_start = datetime.strptime(f"{day}{hour}", "%Y%m%d%H").replace(tzinfo=timezone.utc)

        # The "latest run" check only confirms f000 has been published — a
        # run that started a few hours ago may not have generated/uploaded
        # far-out forecast hours yet, which 404s. Reproduced directly: an
        # 18z run ~3-4h old 404'd on f042/f048. Fall back to older 6-hourly
        # cycles for the SAME target valid time, which have had more time
        # to publish that far out.
        last_exc = None
        for _ in range(_MAX_OLDER_RUN_FALLBACKS + 1):
            try:
                return self._fetch_from_run(bbox, run_start, fstep)
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
                last_exc = e
                run_start -= timedelta(hours=6)
                hours_ahead = (step.valid_time - run_start).total_seconds() / 3600
                fstep = max(0, min(int(round(hours_ahead / STEP_HOURS) * STEP_HOURS), MAX_FORECAST_HOURS))
        raise last_exc

    def _fetch_from_run(self, bbox: BBox, run_start: datetime, fstep: int) -> xr.Dataset:
        day, hour = run_start.strftime("%Y%m%d"), run_start.strftime("%H")
        left = bbox.west % 360
        right = bbox.east % 360
        if right <= left:
            right += 360

        url = (
            "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl?"
            f"file=gfs.t{hour}z.pgrb2.0p25.f{fstep:03d}&all_lev=on&var_UGRD=on&var_VGRD=on"
            f"&subregion=&leftlon={left}&rightlon={right}"
            f"&toplat={bbox.north}&bottomlat={bbox.south}"
            f"&dir=%2Fgfs.{day}%2F{hour}%2Fatmos"
        )
        with urllib.request.urlopen(url, timeout=30) as r:
            data = r.read()

        fd, tmp_path = tempfile.mkstemp(suffix=".grib2")
        os.close(fd)
        try:
            with open(tmp_path, "wb") as f:
                f.write(data)
            with GRIB_DECODE_LOCK:  # eccodes decode is not thread-safe
                ds = xr.open_dataset(
                    tmp_path, engine="cfgrib",
                    filter_by_keys={"typeOfLevel": "heightAboveGround", "level": 10},
                ).load()
        finally:
            os.remove(tmp_path)

        ds = ds.assign_coords(longitude=(((ds.longitude + 180) % 360) - 180)).sortby("longitude")
        return ds
