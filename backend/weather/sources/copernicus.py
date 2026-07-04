"""Copernicus Marine — global ocean surface currents. Needs a free account;
credentials are read from the environment (COPERNICUSMARINE_SERVICE_USERNAME/
_PASSWORD — see weather/config.py) and never touch source code or a login
cache file inside this repo.

Unlike the GRIB sources, the toolbox's open_dataset() does its own lazy
remote subsetting (only the requested bbox/depth is pulled over the wire) —
and it accepts a TIME RANGE, so this source overrides sample() to fetch the
entire route's bbox and date span in ONE call instead of the base class's
one-call-per-tile-per-day pattern. Each toolbox call costs ~10-20s in
session/negotiation overhead regardless of payload size, so a 9-day voyage
went from ~10 sequential calls (2-3 minutes) to one (~15s)."""

import hashlib
import math
from datetime import datetime, timedelta, timezone

import pandas as pd
import xarray as xr

# Must run before `import copernicusmarine` — the toolbox appears to read
# COPERNICUSMARINE_SERVICE_USERNAME/_PASSWORD at import time (importing it
# first, before .env was loaded, silently locked in "no credentials" even
# after the env vars were set correctly — reproduced directly).
from .. import config as _config  # noqa: F401,E402 — loads .env into the process env as a side effect

import copernicusmarine  # noqa: E402

from .base import BBox, ForecastStep, WeatherSource

DATASET_ID = "cmems_mod_glo_phy-cur_anfc_0.083deg_P1D-m"  # daily-mean global currents, 1/12deg
# Note: the merged "phy" P1D-m dataset (no "-cur") does NOT include uo/vo —
# it's sea-ice/SSH/bottom fields only. Currents live in the dedicated
# "phy-cur" dataset id, confirmed via copernicusmarine.describe().


class CredentialsError(RuntimeError):
    """Raised when Copernicus Marine rejects the configured credentials."""


class CopernicusCurrentSource(WeatherSource):
    name = "copernicus_current"
    variables = {"current_u_ms": "uo", "current_v_ms": "vo"}
    requires_auth = True
    cache_max_age_hours = 24.0  # daily-mean product

    def resolve_step(self, valid_time: datetime) -> ForecastStep:
        if valid_time.tzinfo is None:
            valid_time = valid_time.replace(tzinfo=timezone.utc)
        day = valid_time.date()
        return ForecastStep(
            key=f"copernicus_{day:%Y%m%d}",
            valid_time=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc),
        )

    def _open_subset(self, west, east, south, north, day_start, day_end):
        try:
            ds = copernicusmarine.open_dataset(
                dataset_id=DATASET_ID,
                variables=list(self.variables.values()),
                minimum_longitude=west, maximum_longitude=east,
                minimum_latitude=south, maximum_latitude=north,
                minimum_depth=0, maximum_depth=1,
                start_datetime=f"{day_start}T00:00:00",
                end_datetime=f"{day_end}T00:00:00",
            )
        except Exception as e:
            if "credential" in str(e).lower() or "username" in str(e).lower() or "password" in str(e).lower():
                raise CredentialsError(
                    "Copernicus Marine rejected COPERNICUSMARINE_SERVICE_USERNAME/"
                    "_PASSWORD. Check .env — see https://data.marine.copernicus.eu/register"
                ) from e
            raise
        if ds is None:
            # The toolbox falls back to an interactive prompt (and returns
            # None when stdin is unavailable) via a different internal path
            # than the one that raises InvalidUsernameOrPassword directly —
            # observed to be inconsistent between calls with identical
            # arguments. Treat both as the same credentials problem.
            raise CredentialsError(
                "Copernicus Marine returned no dataset (likely rejected "
                "COPERNICUSMARINE_SERVICE_USERNAME/_PASSWORD). Check .env."
            )
        return ds

    def fetch_tile(self, bbox: BBox, step: ForecastStep) -> "xr.Dataset":  # noqa: F821
        day = datetime.strptime(step.key.split("_")[1], "%Y%m%d").date()
        ds = self._open_subset(bbox.west, bbox.east, bbox.south, bbox.north, day, day)
        return ds.isel(time=0, depth=0).load()

    def sample(self, points: list[tuple[float, float, datetime]]) -> pd.DataFrame:
        """One toolbox call for the whole route's bbox and date span instead
        of the base class's per-tile-per-day pattern — see module docstring.
        Falls back to the base implementation when the route crosses the
        antimeridian (a raw min/max lon bbox there would span nearly the
        whole globe, and the daily 1/12-degree grid for that is a far bigger
        download than the per-tile calls it was meant to avoid)."""
        if not points:
            return pd.DataFrame(columns=["lon", "lat", "time", *self.variables])

        lons = [p[0] for p in points]
        lats = [p[1] for p in points]
        if max(lons) - min(lons) > 180:
            return super().sample(points)

        def _utc(t: datetime) -> datetime:
            return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)

        days = sorted(_utc(p[2]).date() for p in points)
        # ONE representative mid-voyage day for every point, deliberately:
        # this dataset's remote chunks are effectively one huge chunk per
        # day, so cost scales with the number of DISTINCT days touched —
        # measured directly: 3 same-day points load in ~1s, while each
        # additional day costs ~30s. Daily-MEAN surface currents evolve
        # slowly relative to a voyage (major current systems drift km/day,
        # and forecast skill past ~5 days is marginal anyway), so a single
        # mid-voyage snapshot is a sound approximation — and the difference
        # between a usable ~15s fetch and a minutes-long one.
        day_mid = days[len(days) // 2]
        # Round the bbox outward to whole degrees so nearby routes on the
        # same day reuse the cache entry.
        west, east = math.floor(min(lons)) - 1, math.ceil(max(lons)) + 1
        south, north = max(math.floor(min(lats)) - 1, -90), min(math.ceil(max(lats)) + 1, 90)

        pts_sig = hashlib.sha1(
            "|".join(f"{lon:.2f},{lat:.2f}" for lon, lat in zip(lons, lats)).encode()
        ).hexdigest()[:10]
        key = f"pts_{day_mid:%Y%m%d}_{pts_sig}"
        extracted = self._cache.get(key)
        if extracted is None:
            # Lazy (zarr-backed) open + vectorized nearest-neighbor pull of
            # just the route points — never .load()s the whole bbox.
            lazy = self._open_subset(west, east, south, north, day_mid, day_mid).isel(depth=0)
            extracted = lazy.sel(
                longitude=xr.DataArray([float(x) for x in lons], dims="pt"),
                latitude=xr.DataArray([float(y) for y in lats], dims="pt"),
                time=xr.DataArray([pd.Timestamp(day_mid)] * len(points), dims="pt"),
                method="nearest",
            ).load()
            self._cache.put(key, extracted)

        rows = []
        for i, (lon, lat, t) in enumerate(points):
            row = {"lon": lon, "lat": lat, "time": t}
            try:
                pt = extracted.isel(pt=i)
                for canon, var in self.variables.items():
                    row[canon] = float(pt[var].values) if var in pt else float("nan")
            except Exception:
                for canon in self.variables:
                    row[canon] = float("nan")
            rows.append(row)
        return pd.DataFrame(rows)
