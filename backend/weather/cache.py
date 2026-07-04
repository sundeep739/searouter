"""Tile-keyed disk cache for decoded weather grids.

Each fetch is one (source, forecast-step, tile) grid — not one lat/lon point —
so the cache is keyed the same way: a request for a nearby waypoint at the
same forecast step reuses the tile already on disk instead of re-downloading.
Stored as NetCDF regardless of the source's native format (GRIB2 or NetCDF),
so every source shares one cache implementation.
"""

import time
from pathlib import Path

import xarray as xr

CACHE_ROOT = Path(__file__).resolve().parent / "cache"
TILE_DEG = 10.0


def tile_index(lon: float, lat: float, tile_deg: float = TILE_DEG) -> tuple[int, int]:
    return (int(lon // tile_deg), int(lat // tile_deg))


def tile_bbox(tile_i: int, tile_j: int, tile_deg: float = TILE_DEG) -> tuple[float, float, float, float]:
    """Returns (west, south, east, north) for a tile index."""
    west = tile_i * tile_deg
    south = tile_j * tile_deg
    return (west, south, west + tile_deg, south + tile_deg)


class DiskCache:
    """One cache directory per weather source."""

    def __init__(self, source_name: str, max_age_hours: float = 48):
        self.dir = CACHE_ROOT / source_name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_age_hours = max_age_hours

    def _path(self, key: str) -> Path:
        return self.dir / f"{key}.nc"

    def get(self, key: str):
        p = self._path(key)
        if not p.exists():
            return None
        age_hours = (time.time() - p.stat().st_mtime) / 3600
        if age_hours > self.max_age_hours:
            p.unlink(missing_ok=True)
            return None
        ds = xr.load_dataset(p)  # loads fully into memory, closes file handle
        return ds

    def put(self, key: str, ds: xr.Dataset):
        p = self._path(key)
        ds.load().to_netcdf(p)

    def purge_stale(self):
        """Delete cache entries older than max_age_hours — call periodically
        to bound disk usage; correctness doesn't depend on this running."""
        cutoff = time.time() - self.max_age_hours * 3600
        for f in self.dir.glob("*.nc"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
