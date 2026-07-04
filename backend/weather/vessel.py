"""Vessel calm-water speed-power model — the first-class input that
replaces the old generic speed^3 approximation.

Two ways to supply a curve:
  - a table of (speed_through_water_kn, shaft_power_kw) points, interpolated
    piecewise-linear and clamped at the ends (no extrapolation beyond the
    supplied range — supply your table across the speeds you actually plan
    to sail at);
  - an admiralty/cubic-law fit — power_kw = coefficient * speed_kn**exponent
    — derived from a single reference point if you don't have sea-trial
    data. exponent=3.0 is the classic cubic law; real hulls in the
    Froude-number range most merchant ships operate in are often closer to
    3-4.5, adjust if you have better data.

Loading condition (laden/ballast) selects between two curves if both are
supplied. If only a laden curve is given, ballast is derived via the
standard admiralty displacement-scaling approximation (power ∝
displacement^(2/3) at a given speed) — a real naval-architecture
relationship, but still an approximation standing in for actual
ballast-condition sea-trial data.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PowerCurve:
    speeds_kn: tuple[float, ...] | None = None
    powers_kw: tuple[float, ...] | None = None
    coefficient: float | None = None
    exponent: float = 3.0

    def __post_init__(self):
        has_table = self.speeds_kn is not None and self.powers_kw is not None
        has_cubic = self.coefficient is not None
        if has_table == has_cubic:
            raise ValueError("PowerCurve needs exactly one of (speeds_kn & powers_kw) or coefficient")
        if has_table and len(self.speeds_kn) != len(self.powers_kw):
            raise ValueError("speeds_kn and powers_kw must be the same length")
        if has_table and len(self.speeds_kn) < 2:
            raise ValueError("a table curve needs at least 2 points")

    @classmethod
    def from_table(cls, speeds_kn, powers_kw) -> "PowerCurve":
        order = np.argsort(speeds_kn)
        speeds = np.asarray(speeds_kn)[order]
        powers = np.asarray(powers_kw)[order]
        return cls(speeds_kn=tuple(speeds), powers_kw=tuple(powers))

    @classmethod
    def from_reference_point(cls, speed_kn: float, power_kw: float, exponent: float = 3.0) -> "PowerCurve":
        """Admiralty/cubic-law fit through one known (speed, power) point —
        e.g. "at 14kn this ship needs about 8000kW shaft power"."""
        coefficient = power_kw / (speed_kn ** exponent)
        return cls(coefficient=coefficient, exponent=exponent)

    def power_kw(self, speed_kn):
        speed_kn = np.asarray(speed_kn, dtype=float)
        if self.coefficient is not None:
            return self.coefficient * speed_kn ** self.exponent
        return np.interp(speed_kn, self.speeds_kn, self.powers_kw)

    def scaled(self, factor: float) -> "PowerCurve":
        """New curve with every power value multiplied by factor."""
        if self.coefficient is not None:
            return PowerCurve(coefficient=self.coefficient * factor, exponent=self.exponent)
        return PowerCurve(speeds_kn=self.speeds_kn, powers_kw=tuple(p * factor for p in self.powers_kw))


@dataclass(frozen=True)
class Vessel:
    name: str
    lpp_m: float                    # length between perpendiculars — wave resistance scale
    beam_m: float                   # waterline beam — wave resistance scale
    windage_area_m2: float          # frontal projected area above waterline — wind resistance
    displacement_laden_t: float
    displacement_ballast_t: float
    curve_laden: PowerCurve
    curve_ballast: PowerCurve | None = None  # None => derive from curve_laden via displacement scaling
    engine: "Engine | None" = None  # enables SFOC-weighted fuel figures / min-fuel objective

    def power_curve(self, loading: str = "laden") -> PowerCurve:
        if loading not in ("laden", "ballast"):
            raise ValueError(f"loading must be 'laden' or 'ballast', got {loading!r}")
        if loading == "laden":
            return self.curve_laden
        if self.curve_ballast is not None:
            return self.curve_ballast
        scale = (self.displacement_ballast_t / self.displacement_laden_t) ** (2.0 / 3.0)
        return self.curve_laden.scaled(scale)


# ---------------------------------------------------------------- engine / SFOC
#
# SFOC (specific fuel oil consumption, g/kWh) varies with engine load: worst
# at low load, best around 70-85% MCR, rising slightly toward 100%. Engine
# makers publish these curves in every project guide, so a real vessel's
# curve is easy to supply as a table. The generic shape below is a typical
# modern low-speed two-stroke, normalized to its optimum.

_GENERIC_2STROKE_LOADS = (0.25, 0.40, 0.50, 0.65, 0.75, 0.85, 1.00)
_GENERIC_2STROKE_FACTORS = (1.055, 1.025, 1.015, 1.003, 1.000, 1.004, 1.025)


@dataclass(frozen=True)
class SFOCCurve:
    loads: tuple[float, ...]           # fraction of MCR, ascending
    sfoc_g_per_kwh: tuple[float, ...]

    @classmethod
    def from_table(cls, loads, sfoc_g_per_kwh) -> "SFOCCurve":
        order = np.argsort(loads)
        return cls(
            loads=tuple(np.asarray(loads, dtype=float)[order]),
            sfoc_g_per_kwh=tuple(np.asarray(sfoc_g_per_kwh, dtype=float)[order]),
        )

    @classmethod
    def generic_2stroke(cls, sfoc_at_optimum: float = 170.0) -> "SFOCCurve":
        """Typical modern low-speed two-stroke shape scaled to a given
        optimum-point SFOC — supply your engine's own table from its
        project guide for real numbers."""
        return cls(
            loads=_GENERIC_2STROKE_LOADS,
            sfoc_g_per_kwh=tuple(f * sfoc_at_optimum for f in _GENERIC_2STROKE_FACTORS),
        )

    def sfoc(self, load_fraction):
        """g/kWh at a load fraction of MCR; clamped to the curve's ends."""
        return np.interp(np.asarray(load_fraction, dtype=float), self.loads, self.sfoc_g_per_kwh)


@dataclass(frozen=True)
class Engine:
    name: str
    mcr_kw: float                      # maximum continuous rating
    sfoc_curve: SFOCCurve

    def fuel_tonnes(self, power_kw, hours):
        """Fuel burned running at power_kw for hours (both array-friendly):
        grams = kW × h × SFOC(load); returned in tonnes."""
        power_kw = np.asarray(power_kw, dtype=float)
        hours = np.asarray(hours, dtype=float)
        load = np.clip(power_kw / self.mcr_kw, self.sfoc_curve.loads[0], self.sfoc_curve.loads[-1])
        return power_kw * hours * self.sfoc_curve.sfoc(load) / 1e6


# ---------------------------------------------------------------- presets
#
# Illustrative, representative figures for each vessel class — NOT sourced
# from a specific ship's sea trials. Meant as usable defaults; supply your
# own PowerCurve (table or reference point) for a real vessel.

HANDYSIZE_BULKER = Vessel(
    name="Handysize bulker (~35,000 DWT)",
    lpp_m=180.0,
    beam_m=30.0,
    windage_area_m2=800.0,
    displacement_laden_t=45000.0,
    displacement_ballast_t=30000.0,
    curve_laden=PowerCurve.from_reference_point(speed_kn=14.0, power_kw=8000.0, exponent=3.0),
    # service point 8,000 kW ≈ 76% MCR — typical two-stroke installation
    engine=Engine(name="2-stroke diesel, MCR 10,500 kW",
                   mcr_kw=10500.0, sfoc_curve=SFOCCurve.generic_2stroke(172.0)),
)

PANAMAX_CONTAINER = Vessel(
    name="Panamax container ship (~4,500 TEU)",
    lpp_m=275.0,
    beam_m=32.2,  # Panama Canal beam constraint
    windage_area_m2=3000.0,  # high freeboard + stacked containers
    displacement_laden_t=75000.0,
    displacement_ballast_t=50000.0,
    curve_laden=PowerCurve.from_reference_point(speed_kn=22.0, power_kw=28000.0, exponent=3.0),
    # service point 28,000 kW ≈ 78% MCR
    engine=Engine(name="2-stroke diesel, MCR 36,000 kW",
                   mcr_kw=36000.0, sfoc_curve=SFOCCurve.generic_2stroke(168.0)),
)

PRESETS: dict[str, Vessel] = {
    "Handysize bulker": HANDYSIZE_BULKER,
    "Panamax container ship": PANAMAX_CONTAINER,
}
