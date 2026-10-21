"""Review corrections: overrides/<st>.json, keyed by beach id, applied at build.

    {"beach_osm_w123": {"name": "Queen's Beach", "tier": "primary",
                        "water_edge": [[[21.27, -157.82], [21.27, -157.81]]],
                        "reason": "two beaches mapped as one", "date": "2026-10-20"}}

`reason` and `date` are required; `name`, `tier`, and `water_edge` are each
optional. A name override replaces the OSM name (the OSM name is kept in
`override.osm_name`), and is applied before scoring. A tier override is
applied after scoring and wins. A water edge override replaces the detected
water edge (polylines of [lat, lng]) and so the grid's heading and water
distances; it is part of the beach's geometry hash. The app's admin review
map writes these files; they survive every re-pull.
"""

from __future__ import annotations

import json
from pathlib import Path

from .score import TIERS

FIELDS = ("name", "tier", "water_edge")
REQUIRED = ("reason", "date")


def validate(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("an overrides file is an object keyed by beach id")
    for rid, ov in data.items():
        where = f"override {rid}"
        if not isinstance(ov, dict):
            raise ValueError(f"{where}: not an object")
        unknown = set(ov) - set(FIELDS) - set(REQUIRED)
        if unknown:
            raise ValueError(f"{where}: unknown keys {sorted(unknown)}")
        missing = [k for k in REQUIRED if not ov.get(k)]
        if missing:
            raise ValueError(f"{where}: missing {missing}")
        if "tier" in ov and ov["tier"] not in TIERS:
            raise ValueError(f"{where}: tier must be one of {TIERS}")
        if "name" in ov and not (isinstance(ov["name"], str) and ov["name"].strip()):
            raise ValueError(f"{where}: name must be a non-empty string")
        edge = ov.get("water_edge")
        if edge is not None and not (isinstance(edge, list) and all(
                isinstance(line, list) and len(line) >= 2 and all(
                    isinstance(p, list) and len(p) == 2 for p in line) for line in edge)):
            raise ValueError(f"{where}: water_edge is a list of polylines of [lat, lng]")
    return data


def load(path: Path) -> dict:
    return validate(json.loads(path.read_text())) if path.exists() else {}


def apply_name(rec: dict, ov: dict | None) -> None:
    """Before scoring: the corrected name."""
    if ov and ov.get("name"):
        rec["_osm_name"] = rec.get("name")
        rec["name"] = ov["name"]


def apply_tier(rec: dict, ov: dict | None) -> None:
    """After scoring: the corrected tier, and the record of what changed."""
    if not ov:
        rec["override"] = None
        return
    if ov.get("tier"):
        rec["tier"] = ov["tier"]
        rec["seed_space"] = ov["tier"] == "primary"
    rec["override"] = {"fields": [k for k in FIELDS if ov.get(k)], "reason": ov["reason"],
                       "date": ov["date"],
                       **({"osm_name": rec.get("_osm_name")} if ov.get("name") else {})}
