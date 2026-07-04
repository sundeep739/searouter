"""Hazard detection, the filtered-graph safety-detour mechanism, and
safety_reroute's branching. core.compute_route/build_filtered_marnet run
against searoute's real (locally-bundled, no-network) routing graph — only
the live weather sampling is mocked, since that needs network."""

import pytest

pytest.importorskip("xarray", reason="weather stack is Phase 2 — deps not installed yet")

import numpy as np
import pandas as pd
from searoute.searoute import get_graphs

import core
import weather.routing as routing
from weather.routing import (
    build_filtered_marnet,
    hazard_mask,
    hazard_polygons_from_mask,
    safety_reroute,
)
from weather.routing import _buffer_run, _compute_route_with_graph


def _weather_df(wave=None, wind_u=None, wind_v=None):
    n = len(wave)
    return pd.DataFrame({
        "gfs_wave__sig_wave_height_m": wave,
        "gfs_wind__wind_u_ms": wind_u or [0.0] * n,
        "gfs_wind__wind_v_ms": wind_v or [0.0] * n,
        "ecmwf_wind__wind_u_ms": [0.0] * n,
        "ecmwf_wind__wind_v_ms": [0.0] * n,
    })


# --------------------------------------------------------- hazard_mask

def test_hazard_mask_flags_high_wave():
    wx = _weather_df(wave=[1.0, 5.0, 2.0])
    mask = hazard_mask(wx, wave_threshold_m=4.0, wind_threshold_ms=100)
    assert list(mask) == [False, True, False]


def test_hazard_mask_flags_high_wind_from_either_model():
    wx = _weather_df(wave=[0.0, 0.0], wind_u=[20.0, 0.0], wind_v=[0.0, 0.0])
    mask = hazard_mask(wx, wave_threshold_m=100, wind_threshold_ms=17.0)
    assert list(mask) == [True, False]


def test_hazard_mask_treats_nan_as_calm_not_hazardous():
    wx = _weather_df(wave=[np.nan])
    mask = hazard_mask(wx, wave_threshold_m=4.0, wind_threshold_ms=17.0)
    assert list(mask) == [False]


def test_hazard_mask_handles_entirely_missing_source_columns():
    """ECMWF is optional now — a weather frame with no ecmwf_* columns at
    all (source not selected) must not crash, and GFS-only wind must still
    trigger the threshold."""
    wx = pd.DataFrame({
        "gfs_wave__sig_wave_height_m": [0.0, 0.0],
        "gfs_wind__wind_u_ms": [20.0, 0.0],
        "gfs_wind__wind_v_ms": [0.0, 0.0],
    })
    mask = hazard_mask(wx, wave_threshold_m=100, wind_threshold_ms=17.0)
    assert list(mask) == [True, False]


# ------------------------------------------------- hazard_polygons_from_mask

def test_hazard_polygons_groups_contiguous_runs_separately():
    coords = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0), (4.0, 0.0)]
    mask = np.array([True, True, False, True, False])
    polygons = hazard_polygons_from_mask(coords, mask, buffer_km=50)
    assert len(polygons) == 2  # [0,1] and [3] are separate hazard runs


def test_hazard_polygons_empty_when_no_hazard():
    coords = [(0.0, 0.0), (1.0, 0.0)]
    mask = np.array([False, False])
    assert hazard_polygons_from_mask(coords, mask, buffer_km=50) == []


# --------------------------------------------------- build_filtered_marnet

def test_build_filtered_marnet_does_not_mutate_shared_singleton():
    shared_before, _ = get_graphs()
    edge_count_before = shared_before.number_of_edges()
    sample_edge = next(iter(shared_before.edges(data=True)))
    u, v, data_before = sample_edge
    weight_before = data_before.get("weight")

    hazard = _buffer_run([u], buffer_deg=5.0)  # a large hazard covering u's edges
    for _ in range(3):  # repeated calls must never accumulate mutation
        build_filtered_marnet([hazard])

    shared_after, _ = get_graphs()
    assert shared_after is shared_before, "get_graphs() must still return the same cached singleton"
    assert shared_after.number_of_edges() == edge_count_before
    assert shared_after[u][v]["weight"] == weight_before


def test_build_filtered_marnet_sets_hazard_edges_to_infinite_weight_not_removed():
    """Regression test: the first implementation deleted hazard-crossing
    edges, which can disconnect the graph and made networkx raise a hard
    NetworkXNoPath instead of the graceful no-route path the rest of the
    module expects. Edges must stay in the graph with weight=inf instead."""
    shared, _ = get_graphs()
    u, v, data = next(iter(shared.edges(data=True)))
    hazard = _buffer_run([u], buffer_deg=5.0)

    filtered = build_filtered_marnet([hazard])
    assert filtered.number_of_edges() == shared.number_of_edges(), "edges must be kept, not deleted"
    assert filtered[u][v]["weight"] == float("inf")


def test_build_filtered_marnet_leaves_non_hazard_edges_untouched():
    shared, _ = get_graphs()
    edges = list(shared.edges(data=True))
    u, v, data = edges[len(edges) // 2]  # an edge far from any hazard below

    far_away_hazard = _buffer_run([(0.0, 89.0)], buffer_deg=0.01)  # tiny, near the pole
    filtered = build_filtered_marnet([far_away_hazard])
    assert filtered[u][v]["weight"] == data.get("weight")


# --------------------------------------------------------- reroute mechanics

def test_narrow_hazard_produces_a_successful_detour():
    origin, dest = (-9.14, 38.72), (-74.0, 40.7)  # Lisbon -> New York
    route = core.compute_route(origin[0], origin[1], dest[0], dest[1], (7,))
    mid = route["coords"][len(route["coords"]) // 2]

    hazard = _buffer_run([mid], buffer_deg=30 / 111.0)
    filtered_M = build_filtered_marnet([hazard])
    detour = _compute_route_with_graph(origin, dest, (7,), filtered_M)

    assert detour["coords"], "a narrow hazard must still leave a viable detour"
    assert detour["coords"] != route["coords"]
    assert detour["length_km"] > route["length_km"]


def test_blanket_hazard_yields_no_route_gracefully():
    origin, dest = (-9.14, 38.72), (-74.0, 40.7)
    route = core.compute_route(origin[0], origin[1], dest[0], dest[1], (7,))

    # buffer every waypoint hugely -- no viable detour should exist
    hazard = _buffer_run(route["coords"], buffer_deg=20.0)
    filtered_M = build_filtered_marnet([hazard])
    detour = _compute_route_with_graph(origin, dest, (7,), filtered_M)

    assert detour["coords"] == []  # graceful "no path", not an exception
    assert detour["length_km"] == 0.0


# --------------------------------------------------------- safety_reroute

def test_safety_reroute_identical_detour_reports_failure_not_reroute(monkeypatch):
    """If the hazard-filtered graph yields the exact same route, the hazard
    couldn't actually be avoided — that must surface as detour_failed, not
    as 'rerouted' with a zero-km detour (observed live: Algeciras→Jebel Ali
    flagged monsoon waves but the 'detour' was the identical route)."""
    def fake_sample_along_route(coords, speed_knots, departure=None, sources=None):
        return _weather_df(wave=[9.0] * len(coords))  # everything hazardous

    monkeypatch.setattr(routing, "sample_along_route", fake_sample_along_route)

    origin, dest = (4.481, 51.9225), (-9.1393, 38.7223)
    original = core.compute_route(origin[0], origin[1], dest[0], dest[1], (7,))
    monkeypatch.setattr(routing, "_compute_route_with_graph",
                         lambda *a, **k: {k2: (list(v) if isinstance(v, list) else v)
                                           for k2, v in original.items()})

    result = safety_reroute(origin, dest, (7,), speed_knots=20)
    assert result["detour_failed"] is True
    assert result["rerouted"] is False
    assert result["route"]["coords"] == original["coords"]


def test_safety_reroute_no_hazard_passthrough(monkeypatch):
    def fake_sample_along_route(coords, speed_knots, departure=None, sources=None):
        return _weather_df(wave=[0.0] * len(coords))

    monkeypatch.setattr(routing, "sample_along_route", fake_sample_along_route)

    origin, dest = (4.481, 51.9225), (-9.1393, 38.7223)  # Rotterdam -> Lisbon
    result = safety_reroute(origin, dest, (7,), speed_knots=20)

    assert result["rerouted"] is False
    assert result["hazards"] == []
    assert result["detour_failed"] is False
    assert result["route"]["coords"]
