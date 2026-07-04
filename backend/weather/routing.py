"""Weather-aware routing, layered entirely on top of core.py and searoute —
neither is modified. Two things live here:

1. Route weather annotation: sample_along_route() (weather/sample.py) applied
   to an already-computed core.py route, for display/inspection.
2. Safety detour: if any waypoint's forecast exceeds the wave/wind
   threshold, build a copy of the shared Marnet graph with hazard-crossing
   edges removed and re-route through it — generalizing exactly the
   mechanism searoute already uses for named chokepoint restrictions
   (Marnet.query() filters edges by `passage`; this filters by hazard
   polygon instead), without ever mutating the cached singleton graph.

safety_reroute() is intentionally NOT lru_cache'd like core.compute_route —
each call's hazard-filtered graph is a fresh, one-off object built from
live weather, so caching it would only pollute core.compute_route's cache
with unique never-reused graphs; there is no meaningful "cache hit" case
for a hazard-driven reroute.
"""

import warnings
from datetime import datetime

import numpy as np
import pandas as pd
import searoute as sr
from searoute.classes.marnet import Marnet
from searoute.searoute import get_graphs
from shapely.geometry import LineString, Point

import core
from .sample import sample_along_route, subsample_waypoints

DEFAULT_WAVE_THRESHOLD_M = 4.0     # ~Douglas sea state "rough" and above
DEFAULT_WIND_THRESHOLD_MS = 17.0   # ~Beaufort 8 (gale)
DEFAULT_BUFFER_KM = 75.0
DEFAULT_SAMPLE_INTERVAL_HOURS = 18.0
MAX_REROUTE_ITERATIONS = 2
_KM_PER_DEGREE = 111.0  # rough equatorial approximation, fine for a buffer radius


def _col_or_zero(weather: pd.DataFrame, name: str) -> pd.Series:
    """Zero-filled Series when a column is absent entirely (e.g. the ECMWF
    source not selected at all) — DataFrame.get(name, 0) returns a bare
    scalar 0 in that case, and 0.fillna() crashes."""
    if name in weather.columns:
        return weather[name].fillna(0)
    return pd.Series(0.0, index=weather.index)


def hazard_mask(weather: pd.DataFrame, wave_threshold_m: float = DEFAULT_WAVE_THRESHOLD_M,
                 wind_threshold_ms: float = DEFAULT_WIND_THRESHOLD_MS) -> np.ndarray:
    """True where EITHER model's wind, or the wave model's Hs, exceeds
    threshold — conservative (any source triggering is enough), since this
    feeds a safety feature. Requiring both GFS and ECMWF to agree is a
    stricter variant worth adding later to cut false positives, not the
    right default for a hazard flag."""
    wave = _col_or_zero(weather, "gfs_wave__sig_wave_height_m")
    gfs_wind = np.hypot(
        _col_or_zero(weather, "gfs_wind__wind_u_ms"),
        _col_or_zero(weather, "gfs_wind__wind_v_ms"),
    )
    ecmwf_wind = np.hypot(
        _col_or_zero(weather, "ecmwf_wind__wind_u_ms"),
        _col_or_zero(weather, "ecmwf_wind__wind_v_ms"),
    )
    wind_speed = np.maximum(gfs_wind, ecmwf_wind)
    return ((wave > wave_threshold_m) | (wind_speed > wind_threshold_ms)).to_numpy()


def hazard_polygons_from_mask(coords: list[tuple[float, float]], mask: np.ndarray,
                               buffer_km: float = DEFAULT_BUFFER_KM) -> list:
    """Buffers contiguous runs of flagged waypoints into exclusion polygons."""
    buffer_deg = buffer_km / _KM_PER_DEGREE
    polygons = []
    run: list[tuple[float, float]] = []
    for (lon, lat), flagged in zip(coords, mask):
        if flagged:
            run.append((lon, lat))
        else:
            if run:
                polygons.append(_buffer_run(run, buffer_deg))
                run = []
    if run:
        polygons.append(_buffer_run(run, buffer_deg))
    return polygons


def _buffer_run(run: list[tuple[float, float]], buffer_deg: float):
    geom = Point(run[0]) if len(run) == 1 else LineString(run)
    return geom.buffer(buffer_deg)


def _edge_crosses_any(u: tuple, v: tuple, hazard_polygons: list) -> bool:
    if not hazard_polygons:
        return False
    edge = LineString([u, v])
    return any(edge.intersects(poly) for poly in hazard_polygons)


def build_filtered_marnet(hazard_polygons: list) -> Marnet:
    """Copy of the shared (lru_cache-backed) Marnet graph with any edge that
    crosses a hazard polygon set to infinite weight — reads the shared
    singleton, never writes to it; the returned graph is a brand-new Marnet
    instance.

    This mirrors exactly how searoute itself handles named-passage
    restrictions (Marnet's own weight function returns inf for a restricted
    edge but leaves it in the graph) rather than physically deleting hazard
    edges. Deleting edges was the first approach here and it was wrong: it
    can disconnect the graph, and networkx's dijkstra then raises a hard
    NetworkXNoPath exception instead of the graceful "no route" that the
    rest of this module (and core.compute_route) expects to handle via a
    caught warning."""
    shared_M, _ = get_graphs()
    filtered = Marnet()
    for u, v, data in shared_M.edges(data=True):
        edge_data = dict(data)
        if _edge_crosses_any(u, v, hazard_polygons):
            edge_data["weight"] = float("inf")
        filtered.add_edge(u, v, **edge_data)
    filtered.restrictions = shared_M.restrictions
    return filtered


def _compute_route_with_graph(origin_pt, dest_pt, restrictions: tuple, M: Marnet) -> dict:
    """Same call shape/return contract as core.compute_route, but against a
    caller-supplied graph instead of the shared one, and not lru_cache'd —
    see module docstring for why."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        route = sr.searoute(
            list(origin_pt), list(dest_pt), units="km",
            restrictions=list(restrictions), return_passages=True,
            append_orig_dest=True, M=M,
        )
    if any("No path found" in str(w.message) for w in caught):
        return {"coords": [], "length_km": 0.0, "passages": []}
    return {
        "coords": route["geometry"]["coordinates"],
        "length_km": route["properties"]["length"],
        "passages": route["properties"].get("traversed_passages", []),
    }


def safety_reroute(origin_pt: tuple[float, float], dest_pt: tuple[float, float],
                    base_restrictions: tuple, speed_knots: float,
                    departure: datetime | None = None,
                    wave_threshold_m: float = DEFAULT_WAVE_THRESHOLD_M,
                    wind_threshold_ms: float = DEFAULT_WIND_THRESHOLD_MS,
                    buffer_km: float = DEFAULT_BUFFER_KM,
                    sample_interval_hours: float = DEFAULT_SAMPLE_INTERVAL_HOURS,
                    sources: list[type] | None = None) -> dict:
    """Computes the normal route, samples weather along it, and re-routes
    around any hazard found — iterating up to MAX_REROUTE_ITERATIONS times
    since a detour changes ETAs at every downstream waypoint, which can
    shift which forecast step applies there.

    Weather is sampled at ~one point per sample_interval_hours of transit
    (subsample_waypoints), not every raw sea-lane node — a 90-waypoint,
    9-day route was ~70 distinct 3-hourly forecast steps and took nearly
    3 minutes to sample in full; at an 18h interval that's ~12 points and
    a few distinct steps. Set sample_interval_hours=0 to sample every raw
    waypoint (finer hazard detection, much slower).

    Returns: route (final coords/length/passages), weather (DataFrame,
    indexed to weather_coords below — NOT route["coords"]), weather_coords
    (the subsampled points weather was actually evaluated at), rerouted
    (bool), hazards (list of shapely polygons that triggered a detour, []
    if none), detour_failed (True if a hazard was found but no viable
    detour exists around it — original route returned in that case, with
    the hazard still flagged).
    """
    route = core.compute_route(origin_pt[0], origin_pt[1], dest_pt[0], dest_pt[1], base_restrictions)
    if not route["coords"]:
        return {"route": route, "weather": None, "weather_coords": [], "rerouted": False,
                 "hazards": [], "detour_failed": False}

    current = route
    rerouted = False
    hazards: list = []
    weather = None
    sample_coords: list = []

    for _ in range(MAX_REROUTE_ITERATIONS):
        sample_coords = (subsample_waypoints(current["coords"], speed_knots, sample_interval_hours)
                          if sample_interval_hours else list(current["coords"]))
        weather = sample_along_route(sample_coords, speed_knots, departure, sources=sources)
        mask = hazard_mask(weather, wave_threshold_m, wind_threshold_ms)
        if not mask.any():
            return {"route": current, "weather": weather, "weather_coords": sample_coords,
                     "rerouted": rerouted, "hazards": [], "detour_failed": False}

        hazards = hazard_polygons_from_mask(sample_coords, mask, buffer_km)
        filtered_M = build_filtered_marnet(hazards)
        detour = _compute_route_with_graph(origin_pt, dest_pt, base_restrictions, filtered_M)
        if not detour["coords"] or detour["coords"] == current["coords"]:
            # No path at all, OR the "detour" is the identical route — the
            # hazard-weighted edges weren't on any alternative's shortest
            # path (e.g. the flagged waypoint sits in a port approach or a
            # single-lane passage with nothing to route around). Claiming
            # "rerouted, adds 0 km" for an unchanged route is misleading —
            # observed live on Algeciras→Jebel Ali during monsoon-season
            # Arabian Sea waves.
            return {"route": current, "weather": weather, "weather_coords": sample_coords,
                     "rerouted": rerouted, "hazards": hazards, "detour_failed": True}
        current = detour
        rerouted = True

    return {"route": current, "weather": weather, "weather_coords": sample_coords,
             "rerouted": rerouted, "hazards": hazards, "detour_failed": False}
