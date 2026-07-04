"""Phase 2 weather planning: light-client conversions (no network) and the
/weather/plan endpoint wired to the real optimizer with weather mocked."""

import math
import os
from datetime import datetime, timezone

os.environ["AUTH_DISABLED"] = "1"

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import main
from main import app
from weather import openmeteo

client = TestClient(app)

VESSEL = {
    "name": "Test bulker",
    "lpp_m": 180.0,
    "beam_m": 30.0,
    "windage_area_m2": 800.0,
    "displacement_laden_t": 45000.0,
    "displacement_ballast_t": 30000.0,
    "curve_laden": {"coefficient": 2.9, "exponent": 3.0},
    "curve_ballast": None,
    "engine": {
        "name": "2-stroke",
        "mcr_kw": 10500.0,
        "sfoc_loads": [25, 50, 75, 85, 100],
        "sfoc_g_per_kwh": [196, 181, 175, 174, 178],
    },
}


# ---- light client unit tests (no network) ----

def test_wind_uv_from_north():
    # wind FROM the north blows TOWARD the south: v negative, u ~0
    u, v = openmeteo._wind_uv(10.0, 0.0)
    assert abs(u) < 1e-9 and v == pytest.approx(-10.0)


def test_current_uv_toward_east():
    # 3.6 km/h current flowing TOWARD 90° (east): u ~+1 m/s, v ~0
    u, v = openmeteo._current_uv(3.6, 90.0)
    assert u == pytest.approx(1.0, abs=1e-6) and abs(v) < 1e-6


def test_uv_nan_passthrough():
    assert all(math.isnan(x) for x in openmeteo._wind_uv(float("nan"), 10))
    assert all(math.isnan(x) for x in openmeteo._current_uv(5, float("nan")))


def test_subsample_monotonic_eta_and_endpoints():
    coords = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)]
    dep = datetime(2026, 8, 1, tzinfo=timezone.utc)
    pts = openmeteo.subsample(coords, 15.0, dep, interval_hours=6.0)
    assert len(pts) >= 2
    assert pts[0][:2] == (0.0, 0.0) and pts[-1][:2] == (3.0, 0.0)
    etas = [p[2] for p in pts]
    assert etas == sorted(etas)


def test_at_reads_eta_hour():
    loc = {"hourly": {"time": ["2026-08-01T00:00", "2026-08-01T01:00"], "v": [5.0, 9.0]}}
    assert openmeteo._at(loc, datetime(2026, 8, 1, 1, tzinfo=timezone.utc), "v") == 9.0
    # beyond horizon -> NaN
    assert math.isnan(openmeteo._at(loc, datetime(2026, 8, 9, tzinfo=timezone.utc), "v"))


# ---- endpoint tests ----

def test_vessel_presets():
    r = client.get("/vessels/presets")
    assert r.status_code == 200
    presets = r.json()["presets"]
    assert "Handysize bulker" in presets
    assert presets["Handysize bulker"]["lpp_m"] > 0


def _fake_sample(monkeypatch, hazard=True):
    """Return synthetic weather so the endpoint runs the real optimizer
    without any network call."""
    def fake(coords, speed, departure, interval_hours=12.0):
        sub = [(0.0, 50.0), (5.0, 48.0), (10.0, 46.0), (15.0, 44.0), (20.0, 42.0)]
        waves = [1.0, 2.0, 5.5 if hazard else 2.5, 2.0, 1.5]
        rows = []
        for i, (lon, lat) in enumerate(sub):
            rows.append({
                "lon": lon, "lat": lat,
                "eta": f"2026-08-0{i + 1}T00:00:00+00:00",
                "gfs_wind__wind_u_ms": 3.0, "gfs_wind__wind_v_ms": -4.0,
                "wind_speed_ms": 5.0,
                "gfs_wave__sig_wave_height_m": waves[i],
                "gfs_wave__wave_period_s": 8.0,
                "gfs_wave__wave_from_direction_deg": 200.0,
                "copernicus_current__current_u_ms": 0.3,
                "copernicus_current__current_v_ms": -0.1,
                "current_speed_ms": 0.32,
            })
        return sub, pd.DataFrame(rows)
    monkeypatch.setattr(openmeteo, "sample_route", fake)


def test_weather_plan_fixed_speed(monkeypatch):
    _fake_sample(monkeypatch, hazard=True)
    r = client.post("/weather/plan", json={
        "origin": "NLRTM", "dest": "CNSGH", "speed": 14,
        "departure": "2026-08-01T00:00:00", "vessel": VESSEL,
        "loading": "laden", "objective": "steady_power",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["vessel"] == "Test bulker"
    assert len(body["legs"]) == 4  # 5 points -> 4 segments
    assert body["hazards"]["count"] >= 1  # the 5.5 m wave leg
    assert body["hazards"]["max_wave_m"] == 5.5
    # optimizer produced per-leg speeds and a fuel comparison
    assert all(leg["stw_knots"] > 0 for leg in body["legs"])
    assert body["plan"]["fuel_baseline_t"] is not None
    # no NaN leaked into JSON
    for leg in body["legs"]:
        for v in leg.values():
            assert v == v  # NaN != NaN


def test_weather_plan_min_fuel_schedule(monkeypatch):
    _fake_sample(monkeypatch, hazard=False)
    r = client.post("/weather/plan", json={
        "origin": "NLRTM", "dest": "CNSGH",
        "schedule": {"etd": "2026-08-01T00:00:00", "eta": "2026-08-20T00:00:00"},
        "vessel": VESSEL, "objective": "min_fuel",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["objective"] == "min_fuel"
    assert body["plan"]["sfoc_modeled"] is True
    assert body["hazards"]["count"] == 0


def test_weather_plan_bad_vessel(monkeypatch):
    _fake_sample(monkeypatch)
    r = client.post("/weather/plan", json={
        "origin": "NLRTM", "dest": "CNSGH", "speed": 14,
        "departure": "2026-08-01T00:00:00", "vessel": {"name": "broken"},
    })
    assert r.status_code == 400
