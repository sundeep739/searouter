"""Shared engine for the sea-distance tools: UN/LOCODE data, port search,
and sea routing. No UI dependencies — used by both app.py (Streamlit) and
api.py (Flask)."""

import csv
import glob
import json
import math
import os
import re
from functools import lru_cache

import pandas as pd
import searoute as sr
from rapidfuzz import fuzz, process
from searoute.classes.passages import Passage

DATA_DIR = os.path.join(os.path.dirname(__file__), "unlocode", "csv")

NAUTICAL_MILES_PER_KM = 1 / 1.852

PASSAGE_LABELS = {
    Passage.suez: "Suez Canal",
    Passage.panama: "Panama Canal",
    Passage.malacca: "Strait of Malacca",
    Passage.babalmandab: "Bab-el-Mandeb (Red Sea)",
    Passage.ormuz: "Strait of Hormuz",
    Passage.bosporus: "Bosporus Strait",
    Passage.gibraltar: "Strait of Gibraltar",
    Passage.sunda: "Sunda Strait",
    Passage.south_africa: "Cape of Good Hope",
    Passage.chili: "Chilean Straits (Magellan)",
    Passage.bering: "Bering Strait",
    Passage.northwest: "Northwest Passage",
}

_COORD_RE = re.compile(r"^(\d{2})(\d{2})([NS])\s+(\d{3})(\d{2})([EW])$")

# UN/LOCODE stores local names (København, Wien, Genova), so common English
# exonyms would otherwise not match at all — "copenhagen" must not resolve to
# Conchán, Peru. Codes verified to exist in the loaded dataset; unknown codes
# are ignored at load time.
ALIASES = {
    "copenhagen": "DKCPH",
    "gothenburg": "SEGOT",
    "cologne": "DECGN",
    "munich": "DEMUC",
    "vienna": "ATVIE",
    "warsaw": "PLWAW",
    "moscow": "RUMOW",
    "saint petersburg": "RULED",
    "st petersburg": "RULED",
    "lisbon": "PTLIS",
    "naples": "ITNAP",
    "rome": "ITROM",
    "genoa": "ITGOA",
    "venice": "ITVCE",
    "athens": "GRATH",
    "antwerp": "BEANR",
    "ghent": "BEGNE",
    "the hague": "NLHAG",
    "flushing": "NLVLI",
    "dunkirk": "FRDKK",
    "havana": "CUHAV",
    "algiers": "DZALG",
    "alexandria": "EGALY",
    "bombay": "INBOM",
    "madras": "INMAA",
    "calcutta": "INCCU",
    "saigon": "VNSGN",
    "pusan": "KRPUS",
    "jeddah": "SAJED",
    "belgrade": "RSBEG",
    "kiev": "UAIEV",
}

# Score bonus so that well-known ports outrank obscure same-named places
# (e.g. Odesa, Ukraine above Odessa, Canada).
_MAJOR_PORT_BONUS = 15


def _parse_coordinates(raw: str):
    if not raw:
        return None, None
    m = _COORD_RE.match(raw.strip())
    if not m:
        return None, None
    lat_deg, lat_min, lat_hem, lon_deg, lon_min, lon_hem = m.groups()
    lat = int(lat_deg) + int(lat_min) / 60
    lon = int(lon_deg) + int(lon_min) / 60
    if lat_hem == "S":
        lat = -lat
    if lon_hem == "W":
        lon = -lon
    return lat, lon


def _major_ports() -> dict:
    """UN/LOCODE -> (lon, lat) for the ~4,000 major ports bundled with
    searoute's routing network. Also used to backfill coordinates for seaports
    that UN/LOCODE lists without any (e.g. Algeciras, Jebel Ali)."""
    path = os.path.join(os.path.dirname(sr.__file__), "data", "ports.geojson")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {
        feat["properties"]["port"]: tuple(feat["geometry"]["coordinates"])
        for feat in data["features"]
        if feat["properties"].get("port")
    }


@lru_cache(maxsize=1)
def load_locations() -> pd.DataFrame:
    major_ports = _major_ports()
    rows = []
    pattern = os.path.join(DATA_DIR, "UNLOCODE CodeListPart*.csv")
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.reader(f):
                if len(row) < 12:
                    continue
                country = row[1].strip()
                location = row[2].strip()
                name = row[3].strip()
                name_ascii = row[4].strip()
                function = row[6]
                coords_raw = row[10].strip()

                if not location:
                    continue

                unlocode = country + location
                lat, lon = _parse_coordinates(coords_raw)
                if lat is None:
                    # UN/LOCODE lists thousands of seaports without coordinates
                    # (even Algeciras and Jebel Ali) — backfill from searoute's
                    # port network where possible, otherwise skip the row.
                    if unlocode in major_ports:
                        lon, lat = major_ports[unlocode]
                    else:
                        continue

                rows.append(
                    {
                        "unlocode": unlocode,
                        "name": name,
                        "name_ascii": name_ascii or name,
                        "country": country,
                        "lat": lat,
                        "lon": lon,
                        "is_seaport": len(function) > 0 and function[0] == "1",
                    }
                )

    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset="unlocode", keep="last").reset_index(drop=True)
    df["is_major_port"] = df["unlocode"].isin(major_ports)
    df["label"] = df["name"] + ", " + df["country"] + " (" + df["unlocode"] + ")"
    alias_texts = {}
    for alias, code in ALIASES.items():
        alias_texts[code] = alias_texts.get(code, "") + " " + alias
    df["search_key"] = (
        df["name_ascii"] + " " + df["country"] + " " + df["unlocode"]
        + df["unlocode"].map(alias_texts).fillna("")
    ).str.lower()
    return df


def search(df: pd.DataFrame, query: str, seaports_only: bool = True, limit: int = 8) -> pd.DataFrame:
    query = query.strip().lower()
    if not query:
        return df.iloc[0:0]

    candidates = df[df["is_seaport"]] if seaports_only else df
    if candidates.empty:
        candidates = df

    matches = process.extract(
        query,
        candidates["search_key"].to_dict(),
        scorer=fuzz.WRatio,
        limit=limit * 4,
    )
    ranked = sorted(
        matches,
        key=lambda m: m[1] + (_MAJOR_PORT_BONUS if candidates.at[m[2], "is_major_port"] else 0),
        reverse=True,
    )
    idx = [i for _, _, i in ranked[:limit]]
    result = candidates.loc[idx]

    if seaports_only and result.empty:
        return search(df, query, seaports_only=False, limit=limit)

    return result


def resolve(query: str, min_score: int = 60):
    """Resolve free text to a single location: exact UN/LOCODE first, then
    best fuzzy match. Unlike search(), refuses low-confidence matches so a
    garbage query can't silently resolve to a random port.
    Returns (row, how) or (None, None)."""
    df = load_locations()
    code = query.strip().upper()
    exact = df[df["unlocode"] == code]
    if len(exact):
        return exact.iloc[0], "exact_code"
    alias_code = ALIASES.get(query.strip().lower())
    if alias_code:
        alias_match = df[df["unlocode"] == alias_code]
        if len(alias_match):
            return alias_match.iloc[0], "alias"
    matches = search(df, query, limit=1)
    if matches.empty:
        return None, None
    row = matches.iloc[0]
    if fuzz.WRatio(query.strip().lower(), row["search_key"]) < min_score:
        return None, None
    return row, "fuzzy_name"


@lru_cache(maxsize=4096)
def compute_route(origin_lon, origin_lat, dest_lon, dest_lat, restrictions: tuple):
    """Cached — callers must treat the returned dict as read-only.

    append_orig_dest makes the route start/end at the actual port coordinates
    instead of the nearest routing-network node — essential for short hops
    (e.g. Copenhagen–Malmö) where the bare network route doesn't touch either
    port. With that flag searoute fabricates a 2-point line even when no path
    exists, so "no route" must be detected via the library's warning instead."""
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        route = sr.searoute(
            [origin_lon, origin_lat],
            [dest_lon, dest_lat],
            units="km",
            restrictions=list(restrictions),
            return_passages=True,
            append_orig_dest=True,
        )
    if any("No path found" in str(w.message) for w in caught):
        return {"coords": [], "length_km": 0.0, "passages": []}
    return {
        "coords": route["geometry"]["coordinates"],
        "length_km": route["properties"]["length"],
        "passages": route["properties"].get("traversed_passages", []),
    }


def compute_alternatives(origin_pt, dest_pt, base_restrictions, main_route):
    """For each passage the main route uses, try a route that avoids it.
    Points are (lon, lat) tuples."""
    alternatives = []
    seen = {(round(main_route["length_km"]), frozenset(main_route["passages"]))}
    for passage in main_route["passages"]:
        if passage in base_restrictions:
            continue
        restrictions = tuple(sorted(set(base_restrictions) | {passage}))
        route = compute_route(origin_pt[0], origin_pt[1], dest_pt[0], dest_pt[1], restrictions)
        if not route["coords"] or not route["length_km"]:
            continue
        key = (round(route["length_km"]), frozenset(route["passages"]))
        if key in seen:
            continue
        seen.add(key)
        alternatives.append({"avoided": passage, **route})
    return alternatives


def passage_names(passages):
    return [PASSAGE_LABELS.get(p, p) for p in passages]


def format_transit(hours: float) -> str:
    """Human duration: hours below two days, days above."""
    if hours < 48:
        return f"{hours:.1f} h"
    return f"{hours / 24:.1f} days"


def route_variants(origin_pt, dest_pt, base_restrictions):
    """Fastest route plus deduplicated chokepoint alternatives.
    Returns [] when no route exists. Points are (lon, lat)."""
    main = compute_route(origin_pt[0], origin_pt[1], dest_pt[0], dest_pt[1], base_restrictions)
    if not main["coords"] or not main["length_km"]:
        return []
    variants = [{"name": "fastest", "avoided": None, **main}]
    for alt in compute_alternatives(origin_pt, dest_pt, base_restrictions, main):
        variants.append({"name": f"avoiding_{alt['avoided']}", **alt})
    return variants


def assess_reported(variants, reported_nm, tolerance_pct):
    """Check a ship-reported distance against every plausible route variant.
    A report matching ANY variant within tolerance is confirmed — ships may
    legitimately take an alternative routing."""
    assessments = []
    for v in variants:
        nm = v["length_km"] * NAUTICAL_MILES_PER_KM
        deviation_pct = (reported_nm - nm) / nm * 100
        assessments.append(
            {
                "name": v["name"],
                "distance_km": round(v["length_km"], 1),
                "distance_nm": round(nm, 1),
                "deviation_pct": round(deviation_pct, 2),
                "within_tolerance": abs(deviation_pct) <= tolerance_pct,
                "via": passage_names(v["passages"]),
            }
        )
    best = min(assessments, key=lambda a: abs(a["deviation_pct"]))
    verdict = "confirmed" if best["within_tolerance"] else "mismatch"
    return {"verdict": verdict, "best_match": best, "assessments": assessments}


def _haversine_km(a, b):
    lon1, lat1 = math.radians(a[0]), math.radians(a[1])
    lon2, lat2 = math.radians(b[0]), math.radians(b[1])
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371 * math.asin(math.sqrt(h))


def order_waypoints(origin_pt, dest_pt, waypoints):
    """Insert each waypoint where it detours the route least, so users can
    drop pins in any order without the route zig-zagging."""
    sequence = [origin_pt, dest_pt]
    for wp in waypoints:
        best_pos, best_added = 1, float("inf")
        for pos in range(1, len(sequence)):
            a, b = sequence[pos - 1], sequence[pos]
            added = _haversine_km(a, wp) + _haversine_km(wp, b) - _haversine_km(a, b)
            if added < best_added:
                best_added, best_pos = added, pos
        sequence.insert(best_pos, wp)
    return sequence


def compute_legs(sequence, restrictions):
    legs = []
    for a, b in zip(sequence, sequence[1:]):
        leg = compute_route(a[0], a[1], b[0], b[1], restrictions)
        if not leg["coords"] or not leg["length_km"]:
            return legs, (a, b)
        legs.append(leg)
    return legs, None
