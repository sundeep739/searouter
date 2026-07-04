"""Persistent vessel library — user-defined vessels (full speed-power
tables, engine/SFOC curves) saved to fleet.json in the project root so
they survive restarts and show up in the Route planner's vessel dropdown
alongside the built-in presets. fleet.json is gitignored: it's local user
data, like .env.
"""

from __future__ import annotations

import json
import os

from .vessel import Engine, PowerCurve, SFOCCurve, Vessel

FLEET_PATH = os.path.join(os.path.dirname(__file__), "..", "fleet.json")


# ------------------------------------------------------------- serialization

def _power_curve_to_dict(c: PowerCurve) -> dict:
    # from_table stores numpy scalars in the tuples — coerce to plain
    # floats or json.dump raises "Object of type int64 is not serializable"
    if c.coefficient is not None:
        return {"coefficient": float(c.coefficient), "exponent": float(c.exponent)}
    return {"speeds_kn": [float(x) for x in c.speeds_kn],
             "powers_kw": [float(x) for x in c.powers_kw]}


def _power_curve_from_dict(d: dict) -> PowerCurve:
    if "coefficient" in d:
        return PowerCurve(coefficient=d["coefficient"], exponent=d.get("exponent", 3.0))
    return PowerCurve.from_table(d["speeds_kn"], d["powers_kw"])


def _engine_to_dict(e: Engine) -> dict:
    return {
        "name": e.name,
        "mcr_kw": float(e.mcr_kw),
        "sfoc_loads": [float(x) for x in e.sfoc_curve.loads],
        "sfoc_g_per_kwh": [float(x) for x in e.sfoc_curve.sfoc_g_per_kwh],
    }


def _engine_from_dict(d: dict) -> Engine:
    return Engine(
        name=d["name"], mcr_kw=d["mcr_kw"],
        sfoc_curve=SFOCCurve.from_table(d["sfoc_loads"], d["sfoc_g_per_kwh"]),
    )


def vessel_to_dict(v: Vessel) -> dict:
    return {
        "name": v.name,
        "lpp_m": v.lpp_m,
        "beam_m": v.beam_m,
        "windage_area_m2": v.windage_area_m2,
        "displacement_laden_t": v.displacement_laden_t,
        "displacement_ballast_t": v.displacement_ballast_t,
        "curve_laden": _power_curve_to_dict(v.curve_laden),
        "curve_ballast": _power_curve_to_dict(v.curve_ballast) if v.curve_ballast else None,
        "engine": _engine_to_dict(v.engine) if v.engine else None,
    }


def vessel_from_dict(d: dict) -> Vessel:
    return Vessel(
        name=d["name"],
        lpp_m=d["lpp_m"],
        beam_m=d["beam_m"],
        windage_area_m2=d["windage_area_m2"],
        displacement_laden_t=d["displacement_laden_t"],
        displacement_ballast_t=d["displacement_ballast_t"],
        curve_laden=_power_curve_from_dict(d["curve_laden"]),
        curve_ballast=_power_curve_from_dict(d["curve_ballast"]) if d.get("curve_ballast") else None,
        engine=_engine_from_dict(d["engine"]) if d.get("engine") else None,
    )


# ---------------------------------------------------------------- fleet file

def _read_raw() -> dict:
    if not os.path.exists(FLEET_PATH):
        return {}
    try:
        with open(FLEET_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def load_fleet() -> dict[str, Vessel]:
    """name -> Vessel for every saved vessel; silently skips entries that
    no longer deserialize (e.g. hand-edited fleet.json)."""
    out = {}
    for name, d in _read_raw().items():
        try:
            out[name] = vessel_from_dict(d)
        except Exception:
            continue
    return out


def save_vessel(vessel: Vessel) -> None:
    raw = _read_raw()
    raw[vessel.name] = vessel_to_dict(vessel)
    with open(FLEET_PATH, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2)


def delete_vessel(name: str) -> bool:
    raw = _read_raw()
    if name not in raw:
        return False
    del raw[name]
    with open(FLEET_PATH, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2)
    return True
