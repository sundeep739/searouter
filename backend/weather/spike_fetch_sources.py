"""Spike: confirm we can pull + decode a real sample from each of the four
planned weather sources, with no design commitments beyond that. Run directly:

    python weather/spike_fetch_sources.py

Prints latency, payload size, and a decoded value for each source so we know
the ingestion path (download -> GRIB2/NetCDF decode -> point lookup) actually
works before building the real abstraction layer.

Requires: cfgrib, eccodes, xarray, netCDF4, ecmwf-opendata, copernicusmarine,
requests (all pip-installable on Windows; pygrib is NOT used — it needs a C++
build toolchain on Windows and has no prebuilt wheel, whereas cfgrib/eccodes
ship one).
"""

import os
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import xarray as xr

# Sample point: open North Atlantic, west of Brittany — real ocean water,
# clear of the wave/current models' coastal land mask.
SAMPLE_LAT, SAMPLE_LON = 48.0, -15.0
BBOX = {"north": 55, "south": 45, "west": -20, "east": -10}
TMP_DIR = tempfile.gettempdir()


def _get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


# --------------------------------------------------------------- NOAA GFS (wind)

def latest_gfs_run():
    """Find the most recently published GFS run (date, hour) by probing
    NOMADS's directory listing — runs land ~4-5h after cycle time."""
    now = datetime.now(timezone.utc)
    for days_back in (0, 1):
        day = (now - timedelta(days=days_back)).strftime("%Y%m%d")
        for hour in ("18", "12", "06", "00"):
            url = (
                f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/"
                f"gfs.{day}/{hour}/atmos/gfs.t{hour}z.pgrb2.0p25.f000"
            )
            try:
                req = urllib.request.Request(url, method="HEAD")
                urllib.request.urlopen(req, timeout=10)
                return day, hour
            except Exception:
                continue
    raise RuntimeError("No recent GFS run found on NOMADS")


def fetch_gfs_wind_sample():
    day, hour = latest_gfs_run()
    url = (
        "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl?"
        f"file=gfs.t{hour}z.pgrb2.0p25.f003&all_lev=on&var_UGRD=on&var_VGRD=on"
        f"&subregion=&leftlon={BBOX['west'] % 360}&rightlon={BBOX['east'] % 360}"
        f"&toplat={BBOX['north']}&bottomlat={BBOX['south']}"
        f"&dir=%2Fgfs.{day}%2F{hour}%2Fatmos"
    )
    t0 = time.time()
    data = _get(url)
    elapsed = time.time() - t0

    # cfgrib/eccodes require a real file path (they build a sidecar .idx
    # file next to it) — an in-memory BytesIO doesn't work here.
    tmp_path = os.path.join(TMP_DIR, "_spike_gfswind.grib2")
    with open(tmp_path, "wb") as f:
        f.write(data)
    ds = xr.open_dataset(
        tmp_path, engine="cfgrib",
        filter_by_keys={"typeOfLevel": "heightAboveGround", "level": 10},
    )
    pt = ds.sel(latitude=SAMPLE_LAT, longitude=SAMPLE_LON % 360, method="nearest").load()
    os.remove(tmp_path)
    return {
        "source": "NOAA GFS (wind, NOMADS filter)",
        "run": f"gfs.{day}/{hour}z",
        "bytes": len(data),
        "seconds": round(elapsed, 2),
        "sample": {"u10_m_s": float(pt["u10"]), "v10_m_s": float(pt["v10"])},
    }


# ------------------------------------------------------------ NOAA GFS-Wave

def fetch_gfs_wave_sample():
    day, hour = latest_gfs_run()
    url = (
        "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfswave.pl?"
        f"file=gfswave.t{hour}z.global.0p25.f003.grib2&var_HTSGW=on&var_WIND=on"
        f"&subregion=&leftlon={BBOX['west'] % 360}&rightlon={BBOX['east'] % 360}"
        f"&toplat={BBOX['north']}&bottomlat={BBOX['south']}"
        f"&dir=%2Fgfs.{day}%2F{hour}%2Fwave%2Fgridded"
    )
    t0 = time.time()
    data = _get(url)
    elapsed = time.time() - t0

    # GFS-Wave GRIB2 mixes surface wind-speed and wave fields in one message
    # set; cfgrib needs open_datasets (plural) to split them cleanly.
    import cfgrib
    tmp_path = os.path.join(TMP_DIR, "_spike_gfswave.grib2")
    with open(tmp_path, "wb") as f:
        f.write(data)
    ds = cfgrib.open_datasets(tmp_path)[0]
    pt = ds.sel(latitude=SAMPLE_LAT, longitude=SAMPLE_LON % 360, method="nearest").load()
    os.remove(tmp_path)
    return {
        "source": "NOAA GFS-Wave (NOMADS filter)",
        "run": f"gfswave.{day}/{hour}z",
        "bytes": len(data),
        "seconds": round(elapsed, 2),
        "sample": {
            "sig_wave_height_m": float(pt["swh"]),
            "wind_speed_m_s": float(pt["ws"]),
        },
    }


# --------------------------------------------------------------- ECMWF Open Data

def fetch_ecmwf_wind_sample():
    from ecmwf.opendata import Client

    tmp_path = os.path.join(TMP_DIR, "_spike_ecmwf.grib2")
    client = Client(source="ecmwf")
    t0 = time.time()
    client.retrieve(type="fc", step=0, param=["10u", "10v"], target=tmp_path)
    elapsed = time.time() - t0

    ds = xr.open_dataset(tmp_path, engine="cfgrib")
    pt = ds.sel(latitude=SAMPLE_LAT, longitude=SAMPLE_LON, method="nearest").load()
    size = os.path.getsize(tmp_path)
    os.remove(tmp_path)
    return {
        "source": "ECMWF Open Data (IFS, global grid)",
        "run": "latest oper fc, step 0h",
        "bytes": size,
        "seconds": round(elapsed, 2),
        "sample": {"u10_m_s": float(pt["u10"]), "v10_m_s": float(pt["v10"])},
        "note": "No server-side bbox subsetting — whole global grid downloaded "
                "(~1.6MB per param/step) then subset locally.",
    }


# ---------------------------------------------------------- Copernicus Marine

def fetch_copernicus_current_sample():
    import copernicusmarine

    if not (os.environ.get("COPERNICUSMARINE_SERVICE_USERNAME")
            and os.environ.get("COPERNICUSMARINE_SERVICE_PASSWORD")):
        return {
            "source": "Copernicus Marine (ocean currents)",
            "status": "SKIPPED — needs free account",
            "note": "Set COPERNICUSMARINE_SERVICE_USERNAME / "
                    "COPERNICUSMARINE_SERVICE_PASSWORD (register at "
                    "https://data.marine.copernicus.eu/register), then rerun.",
        }

    t0 = time.time()
    ds = copernicusmarine.open_dataset(
        dataset_id="cmems_mod_glo_phy_anfc_0.083deg_P1D-m",
        variables=["uo", "vo"],
        minimum_longitude=BBOX["west"], maximum_longitude=BBOX["east"],
        minimum_latitude=BBOX["south"], maximum_latitude=BBOX["north"],
        minimum_depth=0, maximum_depth=1,
    )
    elapsed = time.time() - t0
    pt = ds.sel(latitude=SAMPLE_LAT, longitude=SAMPLE_LON, method="nearest").isel(time=0, depth=0)
    return {
        "source": "Copernicus Marine (ocean currents)",
        "seconds": round(elapsed, 2),
        "sample": {"u_current_m_s": float(pt["uo"]), "v_current_m_s": float(pt["vo"])},
    }


if __name__ == "__main__":
    fetchers = [
        fetch_gfs_wind_sample,
        fetch_gfs_wave_sample,
        fetch_ecmwf_wind_sample,
        fetch_copernicus_current_sample,
    ]
    for fn in fetchers:
        print(f"\n=== {fn.__name__} ===")
        try:
            result = fn()
            for k, v in result.items():
                print(f"  {k}: {v}")
        except Exception as e:
            print(f"  FAILED: {type(e).__name__}: {e}")
