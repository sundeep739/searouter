"""NOAA GFS-Wave (WaveWatch III successor, coupled into GFS) — significant
wave height + the wave model's own surface wind speed, via NOMADS's GRIB
filter service. Free, no auth, same bbox-subsetting pattern as gfs_wind."""

import os
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import cfgrib
import xarray as xr

from .base import GRIB_DECODE_LOCK, BBox, ForecastStep, WeatherSource

STEP_HOURS = 3
MAX_FORECAST_HOURS = 384
_RUN_CACHE_TTL_S = 3600
_MAX_OLDER_RUN_FALLBACKS = 4  # up to 24h of older 6-hourly cycles


class GFSWaveSource(WeatherSource):
    name = "gfs_wave"
    variables = {
        "sig_wave_height_m": "swh",
        "wave_model_wind_speed_ms": "ws",
        # PERPW/DIRPW needed for the encounter-angle-aware added-resistance
        # model — DIRPW confirmed (against WDIR, cross-checked on real data
        # where locally wind-driven seas track the wind closely) to use the
        # same "from" convention as wind direction: degrees clockwise from
        # true north that the waves are arriving FROM.
        "wave_period_s": "perpw",
        "wave_from_direction_deg": "dirpw",
    }
    requires_auth = False
    cache_max_age_hours = 6.0

    def __init__(self):
        super().__init__()
        self._run_cache: tuple[str, str, float] | None = None

    def _latest_run(self) -> tuple[str, str]:
        if self._run_cache and time.time() - self._run_cache[2] < _RUN_CACHE_TTL_S:
            return self._run_cache[0], self._run_cache[1]
        now = datetime.now(timezone.utc)
        for days_back in (0, 1):
            day = (now - timedelta(days=days_back)).strftime("%Y%m%d")
            for hour in ("18", "12", "06", "00"):
                url = (
                    "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/"
                    f"gfs.{day}/{hour}/wave/gridded/gfswave.t{hour}z.global.0p25.f000.grib2"
                )
                try:
                    urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=10)
                    self._run_cache = (day, hour, time.time())
                    return day, hour
                except Exception:
                    continue
        raise RuntimeError("No recent GFS-Wave run found on NOMADS")

    def resolve_step(self, valid_time: datetime) -> ForecastStep:
        day, hour = self._latest_run()
        run_start = datetime.strptime(f"{day}{hour}", "%Y%m%d%H").replace(tzinfo=timezone.utc)
        if valid_time.tzinfo is None:
            valid_time = valid_time.replace(tzinfo=timezone.utc)
        hours_ahead = (valid_time - run_start).total_seconds() / 3600
        step = int(round(hours_ahead / STEP_HOURS) * STEP_HOURS)
        step = max(0, min(step, MAX_FORECAST_HOURS))
        return ForecastStep(
            key=f"gfswave_{day}_{hour}z_f{step:03d}",
            valid_time=run_start + timedelta(hours=step),
        )

    def fetch_tile(self, bbox: BBox, step: ForecastStep) -> xr.Dataset:
        _, day, hour_z, fpart = step.key.split("_")
        hour = hour_z[:-1]
        fstep = int(fpart[1:])
        run_start = datetime.strptime(f"{day}{hour}", "%Y%m%d%H").replace(tzinfo=timezone.utc)

        # Same issue as gfs_wind.py: "latest run" only confirms f000 exists —
        # a run that started recently may not have far-out hours published
        # yet. Fall back to older 6-hourly cycles for the same valid time.
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
            "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfswave.pl?"
            f"file=gfswave.t{hour}z.global.0p25.f{fstep:03d}.grib2"
            "&var_HTSGW=on&var_WIND=on&var_PERPW=on&var_DIRPW=on"
            f"&subregion=&leftlon={left}&rightlon={right}"
            f"&toplat={bbox.north}&bottomlat={bbox.south}"
            f"&dir=%2Fgfs.{day}%2F{hour}%2Fwave%2Fgridded"
        )
        with urllib.request.urlopen(url, timeout=30) as r:
            data = r.read()

        fd, tmp_path = tempfile.mkstemp(suffix=".grib2")
        os.close(fd)
        try:
            with open(tmp_path, "wb") as f:
                f.write(data)
            # GFS-Wave GRIB2 mixes fields with different level types in one
            # message set — open_datasets (plural) splits them cleanly.
            with GRIB_DECODE_LOCK:  # eccodes decode is not thread-safe
                ds = cfgrib.open_datasets(tmp_path)[0].load()
        finally:
            os.remove(tmp_path)

        ds = ds.assign_coords(longitude=(((ds.longitude + 180) % 360) - 180)).sortby("longitude")
        return ds
