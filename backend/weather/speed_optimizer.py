"""Speed-smoothing: redistribute speed-through-water across route segments
to flatten a specific vessel's engine power/load variation — easing off in
bad weather, pushing on in calm weather or with a helping current — while
covering the route in the same total time as a naive constant-speed
passage.

Power per segment = vessel's calm-water power curve at that speed, PLUS
wave added resistance (STAWAVE-1 + encounter-angle/period extensions, see
weather/resistance.py), PLUS wind added resistance (apparent wind — which
genuinely depends on the ship's own candidate speed, unlike current). These
have very different reliability:
  - current assist is exact vector arithmetic: the current vector projected
    onto the segment's own bearing, added straight to speed-over-ground.
  - the calm-water curve is only as good as the vessel input you supply —
    a real sea-trial table is exact for that ship; the built-in presets and
    the admiralty/cubic-law fallback are illustrative approximations.
  - wave/wind added resistance are named empirical methods (see
    resistance.py's docstring for exactly which parts are ITTC-citable vs
    our own extensions), not model-test-calibrated for any specific hull.
"""

import math

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from core import NAUTICAL_MILES_PER_KM, _haversine_km
from .resistance import encounter_angle_rad, wave_added_resistance_n, wind_added_resistance_n
from .vessel import Vessel

MS_PER_KNOT = 0.514444
# Assumed when a vessel has no engine/SFOC profile — a typical modern
# two-stroke figure; with a flat SFOC, "minimum fuel" reduces to minimum
# propulsion energy.
DEFAULT_SFOC_G_PER_KWH = 175.0


def _bearing_vector(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    """Unit (east, north) direction from a to b — planar approximation,
    fine at segment scale (tens to low-hundreds of km)."""
    lon1, lat1 = a
    lon2, lat2 = b
    lat_mid = math.radians((lat1 + lat2) / 2)
    east = (lon2 - lon1) * math.cos(lat_mid)
    north = lat2 - lat1
    norm = math.hypot(east, north)
    return (east / norm, north / norm) if norm else (0.0, 0.0)


def _col_or_nan(weather: pd.DataFrame, col: str) -> pd.Series:
    return weather[col] if col in weather.columns else pd.Series(float("nan"), index=weather.index)


def build_segments(coords: list[tuple[float, float]], weather: pd.DataFrame) -> list[dict]:
    """One entry per (coords[i], coords[i+1]) pair: great-circle distance,
    current assist (fixed — exact vector arithmetic), and the raw
    per-segment weather/geometry a vessel power model needs. Deliberately
    NOT pre-reduced to a single "resistance" scalar like the old generic
    model — wind added resistance depends on the ship's own candidate
    speed (apparent wind), so it has to be evaluated fresh for whatever
    speed the optimizer is trying, not baked in here."""
    hs_col = _col_or_nan(weather, "gfs_wave__sig_wave_height_m")
    period_col = _col_or_nan(weather, "gfs_wave__wave_period_s")
    wave_dir_col = _col_or_nan(weather, "gfs_wave__wave_from_direction_deg")
    wind_u_col = _col_or_nan(weather, "gfs_wind__wind_u_ms")
    wind_v_col = _col_or_nan(weather, "gfs_wind__wind_v_ms")
    cur_u_col = _col_or_nan(weather, "copernicus_current__current_u_ms")
    cur_v_col = _col_or_nan(weather, "copernicus_current__current_v_ms")

    segments = []
    for i in range(len(coords) - 1):
        a, b = coords[i], coords[i + 1]
        dist_km = _haversine_km(a, b)
        heading = _bearing_vector(a, b)

        cu, cv = cur_u_col.iloc[i], cur_v_col.iloc[i]
        current_parallel_ms = 0.0 if (pd.isna(cu) or pd.isna(cv)) else cu * heading[0] + cv * heading[1]

        segments.append({
            "distance_nm": dist_km * NAUTICAL_MILES_PER_KM,
            "current_assist_kn": current_parallel_ms / MS_PER_KNOT,
            "heading_vector": heading,
            "sig_wave_height_m": hs_col.iloc[i],
            "wave_period_s": period_col.iloc[i],
            "wave_from_direction_deg": wave_dir_col.iloc[i],
            "wind_u_ms": wind_u_col.iloc[i],
            "wind_v_ms": wind_v_col.iloc[i],
        })
    return segments


def segment_power_kw(vessel: Vessel, loading: str, segments: list[dict], speed_through_water_kn) -> np.ndarray:
    """Total shaft power (kW) per segment at the given per-segment
    speed-through-water: calm-water curve + wave added resistance + wind
    added resistance. speed_through_water_kn may be a scalar-broadcast
    array (same speed tried across all segments) or one value per segment
    (what the optimizer searches over)."""
    speed_through_water_kn = np.broadcast_to(np.asarray(speed_through_water_kn, dtype=float), (len(segments),))
    curve = vessel.power_curve(loading)
    calm_kw = np.asarray(curve.power_kw(speed_through_water_kn), dtype=float)

    added_kw = np.zeros(len(segments))
    for i, seg in enumerate(segments):
        speed_ms = speed_through_water_kn[i] * MS_PER_KNOT
        heading = seg["heading_vector"]
        wave_from_deg = seg["wave_from_direction_deg"]
        # Unknown wave direction -> assume head seas (angle=0), the
        # conservative (highest-resistance) case, rather than guessing calm.
        angle = encounter_angle_rad(heading, wave_from_deg) if math.isfinite(wave_from_deg) else 0.0

        r_wave_n = wave_added_resistance_n(seg["sig_wave_height_m"], seg["wave_period_s"], angle,
                                            vessel.beam_m, vessel.lpp_m)
        r_wind_n = wind_added_resistance_n(seg["wind_u_ms"], seg["wind_v_ms"], heading, speed_ms,
                                            vessel.windage_area_m2)
        added_kw[i] = (r_wave_n + r_wind_n) * speed_ms / 1000.0

    return calm_kw + added_kw


def smooth_speed(coords: list[tuple[float, float]], weather: pd.DataFrame, vessel: Vessel,
                  nominal_speed_knots: float | None = None, loading: str = "laden",
                  speed_min: float | None = None, speed_max: float | None = None,
                  target_hours: float | None = None, objective: str = "steady_power") -> dict:
    """Solves for per-segment speed-through-water subject to a fixed total
    schedule, under one of two objectives:

      - "steady_power": minimize the variance of shaft power across segments
        (the classic captain's plan — engine-friendly, and near-minimum fuel
        because the convex power curve punishes uneven running);
      - "min_fuel": minimize total fuel, weighting each segment's energy by
        the engine's SFOC at that load (g/kWh vs %MCR). Needs vessel.engine;
        without one, a flat DEFAULT_SFOC_G_PER_KWH is assumed, making this
        equivalent to minimizing total propulsion energy.

    The schedule comes from ONE of:
      - nominal_speed_knots: same total time as sailing that speed uniformly
        (the original behavior), or
      - target_hours: an explicit voyage duration — ETD→ETA planning, where
        the caller derives hours from the schedule and the required average
        speed falls out of the actual route distance here (which matters
        when a weather detour has lengthened the route: the schedule holds,
        the required speed adjusts).

    Returns per-segment speeds/power (kW) alongside the naive-uniform-speed
    baseline, plus fuel totals for both plans.
    """
    segments = build_segments(coords, weather)
    n = len(segments)
    if n == 0:
        return {"success": False, "message": "route has fewer than 2 waypoints", "segment_speeds_knots": []}

    distances = np.array([s["distance_nm"] for s in segments])
    current_assist = np.array([s["current_assist_kn"] for s in segments])
    total_distance_nm = distances.sum()

    if target_hours is not None:
        t_target_hours = float(target_hours)
        nominal_speed_knots = total_distance_nm / t_target_hours  # required average speed
    elif nominal_speed_knots is not None:
        t_target_hours = total_distance_nm / nominal_speed_knots  # naive uniform-speed baseline
    else:
        raise ValueError("smooth_speed needs nominal_speed_knots or target_hours")

    speed_min = speed_min if speed_min is not None else max(3.0, nominal_speed_knots * 0.5)
    speed_max = speed_max if speed_max is not None else nominal_speed_knots * 1.3

    def sog(speed_through_water):
        return np.maximum(speed_through_water + current_assist, 0.5)  # floor: avoid div-by-~0

    def segment_hours(speed_through_water):
        return distances / sog(speed_through_water)

    def power(speed_through_water):
        return segment_power_kw(vessel, loading, segments, speed_through_water)

    def fuel_tonnes(speed_through_water):
        """Total voyage fuel at these speeds — SFOC-weighted when the vessel
        has an engine profile, flat-SFOC energy proxy otherwise."""
        p = power(speed_through_water)
        h = segment_hours(speed_through_water)
        if vessel.engine is not None:
            return float(np.sum(vessel.engine.fuel_tonnes(p, h)))
        return float(np.sum(p * h) * DEFAULT_SFOC_G_PER_KWH / 1e6)

    # SLSQP occasionally fails ("Positive directional derivative for
    # linesearch") from a flat/degenerate starting point, and can even
    # report false success — claiming convergence after zero real progress
    # (reproduced directly: starting exactly at the uniform baseline,
    # SLSQP "succeeded" in 5 iterations with x unchanged from x0 and the
    # objective identical to its starting value). A first version tried a
    # severity-WEIGHTED guess (nominal * mean(severity)/severity), but that
    # can swing wildly for a large severity spread (e.g. 8x between a calm
    # and a stormy segment) — clipping such an extreme raw value back into
    # [speed_min, speed_max] collapsed multiple "different" candidates onto
    # the same boundary point, defeating the point of trying several starts.
    #
    # Rank-based instead: sort segments by severity and assign them an
    # evenly spaced speed between speed_max (mildest) and speed_min
    # (worst) — by construction every candidate this produces already
    # lies inside the bounds, no clipping-collapse possible.
    # The fixed-speed baseline must MEET THE SCHEDULE GIVEN CURRENTS, not
    # just sail the nominal STW: a constant-STW plan arrives late against a
    # net adverse current and early with a favorable one, so comparing the
    # schedule-constrained optimum against it is apples-to-oranges. Caught
    # via a real user voyage (New York→Miami, fighting the Gulf Stream):
    # the "optimized" plan showed -6.7% "savings" purely because the naive
    # baseline was quietly arriving hours later than the schedule the
    # optimizer was being held to. hours(v) is strictly decreasing in v,
    # so bisection finds the schedule-feasible uniform STW.
    def _hours_at_uniform(v: float) -> float:
        return float(np.sum(distances / np.maximum(v + current_assist, 0.5)))

    lo, hi = 0.5, max(speed_max, nominal_speed_knots)
    while _hours_at_uniform(hi) > t_target_hours and hi < 60.0:
        hi *= 1.3
    for _ in range(80):
        mid = (lo + hi) / 2
        if _hours_at_uniform(mid) > t_target_hours:
            lo = mid
        else:
            hi = mid
    baseline_stw = (lo + hi) / 2

    # widen default bounds if the feasible baseline sits outside them
    # (strong net adverse current can demand more than 1.3x nominal)
    speed_min = min(speed_min, max(3.0, baseline_stw * 0.85))
    speed_max = max(speed_max, baseline_stw * 1.15)
    # A very slow required average (e.g. a long schedule on a short route) can
    # push the 3 kn floor above the computed ceiling — keep bounds ordered so
    # the search doesn't crash on an inverted range.
    speed_max = max(speed_max, speed_min + 0.5)

    baseline_x0 = np.full(n, baseline_stw)  # schedule-feasible constant-speed baseline — reported as-is, never overwritten by the search below
    severity = np.array([s["sig_wave_height_m"] if math.isfinite(s["sig_wave_height_m"]) else 0.0
                         for s in segments])
    order = np.argsort(severity)  # ascending: mildest segment first
    ranked_speeds = np.linspace(speed_max, speed_min, n)
    smart_x0 = np.empty(n)
    smart_x0[order] = ranked_speeds

    rng = np.random.default_rng(0)
    candidates = [smart_x0, baseline_x0]
    candidates += [rng.uniform(speed_min, speed_max, n) for _ in range(3)]

    # Normalize the problem to O(1): raw shaft power is tens of thousands
    # of kW while the decision variables are tens of knots, so the raw
    # variance objective is O(1e5+) with O(1) steps — a scaling mismatch
    # that made SLSQP fail with "Positive directional derivative for
    # linesearch" at realistic vessel powers (recurring in the app at its
    # 24kn default). Dividing power by the baseline mean (or fuel by the
    # baseline fuel) and time by the target makes both objective and
    # constraint O(1).
    power_scale = max(float(np.mean(power(baseline_x0))), 1e-9)
    baseline_fuel_t = fuel_tonnes(baseline_x0)

    if objective == "min_fuel":
        fuel_scale = max(baseline_fuel_t, 1e-9)

        def objective_fn(x):
            return fuel_tonnes(x) / fuel_scale
    elif objective == "steady_power":
        def objective_fn(x):
            return float(np.var(power(x) / power_scale))
    else:
        raise ValueError(f"objective must be 'steady_power' or 'min_fuel', got {objective!r}")

    def time_constraint(x):
        return segment_hours(x).sum() / t_target_hours - 1.0

    # Try every candidate rather than stopping at the first reported
    # success — SLSQP can claim "success" while making zero real progress
    # from a starting point (reproduced directly: x0 = baseline_x0 for a
    # 3-segment case "converged" in 5 iterations with x == x0 and an
    # unchanged objective value — a false convergence, not a genuine
    # optimum). Keep whichever successful attempt achieves the lowest
    # objective; only fall back to an unsuccessful one if none succeed.
    best_success, best_success_objective = None, float("inf")
    best_any, best_any_objective = None, float("inf")
    for candidate_x0 in candidates:
        result = minimize(
            objective_fn,
            candidate_x0, method="SLSQP",
            bounds=[(speed_min, speed_max)] * n,
            constraints=[{"type": "eq", "fun": time_constraint}],
            options={"maxiter": 200, "ftol": 1e-10},
        )
        if result.fun < best_any_objective:
            best_any, best_any_objective = result, result.fun
        if result.success and result.fun < best_success_objective:
            best_success, best_success_objective = result, result.fun

    result = best_success if best_success is not None else best_any
    speeds = result.x if result.success else baseline_x0

    def segment_fuel_t(speed_through_water):
        p = power(speed_through_water)
        h = segment_hours(speed_through_water)
        if vessel.engine is not None:
            return vessel.engine.fuel_tonnes(p, h)
        return p * h * DEFAULT_SFOC_G_PER_KWH / 1e6

    return {
        "success": bool(result.success),
        "message": result.message,
        "objective": objective,
        "segment_distances_nm": distances.tolist(),
        "segment_speeds_knots": speeds.tolist(),
        "segment_power_kw": power(speeds).tolist(),
        "baseline_power_kw": power(baseline_x0).tolist(),
        "segment_fuel_t": segment_fuel_t(speeds).tolist(),
        "fuel_optimized_t": fuel_tonnes(speeds),
        "fuel_baseline_t": baseline_fuel_t,
        "sfoc_modeled": vessel.engine is not None,
        "power_std_optimized": float(np.std(power(speeds))),
        "power_std_baseline": float(np.std(power(baseline_x0))),
        "total_hours_optimized": float(segment_hours(speeds).sum()),
        "total_hours_baseline": float(_hours_at_uniform(baseline_stw)),
        "nominal_speed_knots": float(nominal_speed_knots),
        "baseline_speed_knots": float(baseline_stw),
    }
