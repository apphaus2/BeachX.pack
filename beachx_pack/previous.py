"""The previous pack on disk (what git has) is the build's memory.

At the start of a build, pack/beaches.json plus pack/boundaries/, pack/grids/
and pack/nearby/ are read back into per-beach blocks:
{id: {"geometry": {"hash", "pulled_at", "data"}, "nearby": {...}, ...}}.
A block whose incoming hash equals the previous one reuses these outputs; a
block not pulled this run is carried forward with its old hash and pulled_at.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import cells
from .blocks import BLOCKS

# geometry-block fields copied straight from a previous record
CORE_KEYS = (
    "id", "osm_type", "osm_id", "osm_url", "osm_edit_url", "osm_version", "osm_timestamp",
    "osm_changeset", "name", "alt_names", "latitude", "longitude", "bbox", "state",
    "state_confidence", "country", "surface", "access", "fee", "dog", "lifeguard",
    "wheelchair", "nudist", "operator", "website", "wikipedia", "wikipedia_url", "wikidata",
    "wikidata_url", "is_resort", "protected_area_hint", "osm_tags_count", "osm_ids",
    "merged_from", "tags",
)


def _shapes(pack_dir: Path, sub: str) -> dict:
    out: dict = {}
    for path in sorted((pack_dir / sub).glob("*.json")):
        out.update(json.loads(path.read_text()))
    return out


def absolute_heights(recs: list[tuple] | None, rec: dict) -> list[int | None] | None:
    """Per-cell heights in centimetres in the terrain datum, from the stored
    0.1 m steps above the lowest cell."""
    low = rec.get("terrain_lowest_m")
    if not recs or low is None:
        return None
    base = round(low * 100)
    return [None if r[0] == cells.HEIGHT_UNKNOWN else base + r[0] * 10 for r in recs]


def load(pack_dir: Path) -> tuple[dict[str, dict], dict | None]:
    """(blocks per beach id, previous manifest). Empty when there is no pack."""
    path = pack_dir / "beaches.json"
    if not path.exists():
        return {}, None
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    derive = (manifest or {}).get("derive")
    boundaries, stored = _shapes(pack_dir, "boundaries"), _shapes(pack_dir, "grids")
    grids, records = {}, {}
    for rid, g in stored.items():
        if g.get("cell_record_version") == cells.VERSION:
            grids[rid], records[rid] = cells.unpack_grid(g)
        else:
            grids[rid] = g  # an older format: its derive key differs, so it is recomputed
    nearby = _shapes(pack_dir, "nearby")
    out: dict[str, dict] = {}
    for rec in json.loads(path.read_text()):
        rid, marks = rec["id"], rec.get("blocks") or {}
        blocks: dict[str, dict] = {}
        geo = marks.get("geometry")
        if geo:
            blocks["geometry"] = {**geo, "data": {
                "core": {k: rec.get(k) for k in CORE_KEYS},
                "shape": {"area_m2": rec.get("area_m2"), "boundary": boundaries.get(rid),
                          "grid": grids.get(rid), "light": False, "derive": derive,
                          "note": "reused",
                          "water": {"kind": rec.get("water_kind"),
                                    "edge_m": rec.get("water_edge_m"),
                                    "orientation": rec.get("orientation")}},
                "tiles": rec.get("tiles") or []}}
        if marks.get("nearby"):
            blocks["nearby"] = {**marks["nearby"], "data": {"items": nearby.get(rid, [])}}
        for block in BLOCKS[2:]:
            if block.name == "terrain":
                if marks.get("terrain"):
                    blocks["terrain"] = {**marks["terrain"], "data": {
                        "fields": {k: rec.get(k) for k in block.fields},
                        "grid_key": marks["terrain"].get("grid_key"),
                        "heights_cm": absolute_heights(records.get(rid), rec)}}
                continue
            if marks.get(block.name):
                blocks[block.name] = {**marks[block.name],
                                      "data": {"fields": {k: rec.get(k) for k in block.fields}}}
        out[rid] = blocks
    return out, manifest
