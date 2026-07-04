"""Added resistance from waves and wind — replaces the old generic
speed^3 * resistance_multiplier() approximation with named, physically
motivated (if still empirical) formulas.

Wave resistance: STAWAVE-1 (ITTC Recommended Procedure 7.5-04-01-01.1) is
the exact, citable core — but it is officially defined for head seas only
(Fn <= 0.2 merchant hull forms) and has no explicit wave-period term. This
module extends it to arbitrary encounter angles and periods with two
explicitly-labeled engineering approximations (a raised-cosine directional
taper, and a wavelength/Lpp resonance-shaped period factor) — real, but not
themselves ITTC-endorsed. Encounter angle uses real route bearing vs wave
direction from the weather sources.

Wind resistance: a single-coefficient quadratic drag model on the
longitudinal (head/tail) component of apparent wind — not a full ISO 15016
directional Cd(psi) curve (that needs wind-tunnel data per hull form this
project doesn't have). Simple, but real: the ship's own forward speed is
subtracted from true wind first, since a moving ship always feels at least
its own speed as apparent headwind — a fixed-wind approximation misses
this entirely.
"""

from __future__ import annotations

import math

SEAWATER_DENSITY_KG_M3 = 1025.0
AIR_DENSITY_KG_M3 = 1.225
GRAVITY_M_S2 = 9.81
WIND_DRAG_COEFFICIENT = 0.8  # typical ballpark for merchant-ship superstructure windage


def _safe(value: float, default: float = 0.0) -> float:
    return default if value is None or not math.isfinite(value) else value


# --------------------------------------------------------------- geometry

def bearing_to_unit_vector(bearing_deg: float) -> tuple[float, float]:
    """Compass bearing (degrees clockwise from true north) -> (east, north)
    unit vector."""
    rad = math.radians(bearing_deg)
    return (math.sin(rad), math.cos(rad))


def encounter_angle_rad(heading_vector: tuple[float, float], wave_from_bearing_deg: float) -> float:
    """0 = head seas (waves arriving from dead ahead), pi = following seas,
    pi/2 = beam seas. Both DIRPW (wave) and WDIR (wind) use the "from"
    convention — confirmed against real data (DIRPW tracks WDIR closely
    where wind-driven seas dominate)."""
    wave_from_vec = bearing_to_unit_vector(wave_from_bearing_deg)
    dot = heading_vector[0] * wave_from_vec[0] + heading_vector[1] * wave_from_vec[1]
    return math.acos(max(-1.0, min(1.0, dot)))


# ---------------------------------------------------------- wave resistance

def stawave1_head_sea_resistance_n(sig_wave_height_m: float, beam_m: float, lpp_m: float) -> float:
    """STAWAVE-1 head-sea added resistance (Newtons).
    R_AWL = (1/16) * rho_sw * g * Hs^2 * B * sqrt(B / Lbwl)
    Lpp is used as an approximation for L_BWL (length on the waterline)."""
    hs = max(0.0, _safe(sig_wave_height_m))
    return (1.0 / 16.0) * SEAWATER_DENSITY_KG_M3 * GRAVITY_M_S2 * hs ** 2 * beam_m * math.sqrt(beam_m / lpp_m)


def _encounter_angle_factor(angle_rad: float) -> float:
    """Engineering extension of STAWAVE-1 beyond its official head-seas-only
    scope: a raised-cosine taper — 1.0 head seas, ~0.65 beam seas, 0.3
    following seas. Real ships still see meaningful added resistance
    running with the sea, just markedly less than punching into it — this
    is NOT an ITTC-endorsed directional formula, just a reasonable shape."""
    return 0.3 + 0.7 * (0.5 + 0.5 * math.cos(angle_rad))


def _period_factor(wave_period_s: float, lpp_m: float) -> float:
    """Added resistance depends on how the wavelength compares to the
    ship's length — roughly resonant (this factor peaks at 1.0) when
    wavelength ≈ Lpp, falling toward 0.5 for much shorter/longer waves.
    Deep-water dispersion: wavelength = g*T^2/(2*pi). STAWAVE-1's basic
    formula has no period term at all; this is our own bounded, modest
    modulation around it, not a citable standalone method."""
    period = _safe(wave_period_s)
    if period <= 0:
        return 1.0
    wavelength_m = GRAVITY_M_S2 * period ** 2 / (2 * math.pi)
    ratio = wavelength_m / lpp_m
    return 0.5 + 0.5 * math.exp(-((ratio - 1.0) / 0.6) ** 2)


def wave_added_resistance_n(sig_wave_height_m: float, wave_period_s: float,
                             encounter_angle: float, beam_m: float, lpp_m: float) -> float:
    """Total wave added-resistance force (Newtons), STAWAVE-1 magnitude
    scaled by the encounter-angle and period factors above."""
    base = stawave1_head_sea_resistance_n(sig_wave_height_m, beam_m, lpp_m)
    return base * _encounter_angle_factor(encounter_angle) * _period_factor(wave_period_s, lpp_m)


# ---------------------------------------------------------- wind resistance

def apparent_wind_longitudinal_ms(true_wind_u_ms: float, true_wind_v_ms: float,
                                   heading_vector: tuple[float, float],
                                   speed_through_water_ms: float) -> float:
    """Component of ship-relative (apparent) wind along the ship's own
    heading — positive = headwind. u/v wind components use the standard
    meteorological "blowing toward" convention, so the apparent wind vector
    is (true_wind - ship_velocity); a ship moving at 10 m/s in dead calm air
    (true wind zero) then has apparent wind (-10,0)*heading — blowing from
    the bow toward the stern, i.e. a headwind. We want headwind positive,
    so we project (ship_velocity - true_wind) onto heading instead — the
    negation of the apparent-wind vector, which is exactly the "how much
    is the air hitting the bow" quantity. A ship always feels at least its
    own speed as apparent headwind — a fixed-ambient-wind approximation
    misses this effect entirely."""
    wu, wv = _safe(true_wind_u_ms), _safe(true_wind_v_ms)
    ship_u = speed_through_water_ms * heading_vector[0]
    ship_v = speed_through_water_ms * heading_vector[1]
    rel_u, rel_v = ship_u - wu, ship_v - wv
    return rel_u * heading_vector[0] + rel_v * heading_vector[1]


def wind_added_resistance_n(true_wind_u_ms: float, true_wind_v_ms: float,
                             heading_vector: tuple[float, float], speed_through_water_ms: float,
                             windage_area_m2: float, drag_coefficient: float = WIND_DRAG_COEFFICIENT) -> float:
    """Quadratic drag on the longitudinal apparent-wind component, signed
    so a strong following wind can reduce resistance (a real, if modest,
    effect) — smaller in magnitude than wave resistance except for
    high-sided ships (large windage_area_m2), per standard practice."""
    v_rel = apparent_wind_longitudinal_ms(true_wind_u_ms, true_wind_v_ms, heading_vector, speed_through_water_ms)
    return 0.5 * AIR_DENSITY_KG_M3 * drag_coefficient * windage_area_m2 * v_rel * abs(v_rel)
