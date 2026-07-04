"""Endpoint tests against the real routing engine (no network needed —
searoute's graph and the UN/LOCODE CSVs are bundled). Auth is disabled via
AUTH_DISABLED so tests exercise routing, not Supabase."""

import os

os.environ["AUTH_DISABLED"] = "1"

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health_public():
    assert client.get("/health").json() == {"status": "ok"}


def test_search_rotterdam():
    r = client.get("/search", params={"q": "rotterdam", "limit": 5})
    assert r.status_code == 200
    codes = [x["unlocode"] for x in r.json()["results"]]
    assert "NLRTM" in codes


def test_search_alias_copenhagen():
    r = client.get("/search", params={"q": "copenhagen"})
    assert r.json()["results"][0]["unlocode"] == "DKCPH"


def test_route_rotterdam_shanghai():
    r = client.post("/route", json={"origin": "NLRTM", "dest": "shanghai"})
    assert r.status_code == 200
    body = r.json()
    # Both codes are Shanghai: CNSGH is the city entry, CNSHG the port entry.
    assert body["dest"]["unlocode"] in ("CNSGH", "CNSHG")
    fastest = body["variants"][0]
    # Suez routing is ~10,500 nm; allow generous slack for network updates.
    assert 9500 < fastest["distance_nm"] < 11500
    assert "suez" in fastest["passages"]
    # At least one longer alternative that skips Suez must be offered
    # (identical alternatives are deduped by the engine, so the variant may
    # be named after another passage, e.g. avoiding_babalmandab via Panama).
    alts = [v for v in body["variants"] if "suez" not in v["passages"]]
    assert alts and all(v["distance_nm"] > fastest["distance_nm"] for v in alts)


def test_route_avoid_suez():
    r = client.post("/route", json={"origin": "NLRTM", "dest": "CNSGH", "avoid": ["suez"]})
    assert r.status_code == 200
    assert "suez" not in r.json()["variants"][0]["passages"]


def test_route_bad_passage():
    r = client.post("/route", json={"origin": "NLRTM", "dest": "CNSGH", "avoid": ["atlantis"]})
    assert r.status_code == 400


def test_route_unresolvable_port():
    r = client.post("/route", json={"origin": "xqzzt9 nowhere", "dest": "CNSGH"})
    assert r.status_code == 404


def test_route_schedule_required_speed():
    r = client.post(
        "/route",
        json={
            "origin": "NLRTM",
            "dest": "CNSGH",
            "schedule": {"etd": "2026-08-01T00:00:00", "eta": "2026-08-21T00:00:00"},
        },
    )
    body = r.json()
    assert body["schedule"]["target_hours"] == 480
    expected = body["variants"][0]["distance_nm"] / 480
    assert abs(body["schedule"]["required_speed_knots"] - expected) < 0.05


def test_voyage_legs_and_totals():
    r = client.post(
        "/voyage",
        json={
            "ports": ["ESVLC", "ITGOA", "GRPIR"],
            "speed": 17,
            "dwell_hours": 12,
            "departure": "2026-08-01T08:00:00",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["legs"]) == 2
    assert body["total_hours"] == pytest.approx(body["sea_hours"] + 12, abs=0.2)
    assert body["legs"][0]["eta"] < body["legs"][1]["etd"]


def test_matrix():
    r = client.post("/matrix", json={"origins": ["NLRTM", "DEHAM"], "dests": ["CNSGH"]})
    body = r.json()
    assert len(body["cells"]) == 2 and len(body["cells"][0]) == 1
    assert body["cells"][0][0]["distance_nm"] > 9000


def test_verify_confirmed():
    r = client.post(
        "/verify",
        json={"origin": "NLRTM", "dest": "CNSGH", "reported_nm": 10650, "tolerance_pct": 5},
    )
    body = r.json()
    assert body["verdict"] == "confirmed"
    assert abs(body["best_match"]["deviation_pct"]) <= 5


def test_verify_mismatch():
    r = client.post(
        "/verify",
        json={"origin": "NLRTM", "dest": "CNSGH", "reported_nm": 25000, "tolerance_pct": 5},
    )
    assert r.json()["verdict"] == "mismatch"


def test_verify_batch_mixed():
    r = client.post(
        "/verify/batch",
        json={
            "rows": [
                {"origin": "NLRTM", "dest": "CNSGH", "reported_nm": 10650},
                {"origin": "garbage xyz123", "dest": "CNSGH", "reported_nm": 5000},
            ],
            "tolerance_pct": 5,
        },
    )
    results = r.json()["results"]
    assert results[0]["verdict"] == "confirmed"
    assert results[1]["verdict"] == "error"
