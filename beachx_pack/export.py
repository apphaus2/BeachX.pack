"""Write pack/ and the app-facing exports (Dart seed, trimmed JSON)."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from . import PIPELINE_VERSION
from .blocks import BLOCKS
from .hashing import sha256
from .normalize import DERIVE_KEY
from .score import TIERS
from .sources import MODULES

CSV_SKIP = ("tags", "alt_names", "bbox", "blocks", "nearby_counts", "nws_grid", "tiles")
APP_FIELDS = ("id", "name", "latitude", "longitude", "state", "tier", "surface", "access",
              "lifeguard", "seed_space", "osm_type", "osm_id", "osm_url", "osm_version",
              "osm_timestamp", "wikidata", "wikipedia_url", "timezone", "tide_station_id",
              "wave_buoy_id", "county", "place", "public_access", "land_manager_type",
              "designation", "nws_forecast_zone", "nws_grid", "beacon_beach_id", "description")
SHAPE_DIRS = ("boundaries", "grids", "nearby")

OSM_SOURCES = [
    {"name": "OpenStreetMap via the Overpass API", "block": "geometry, nearby",
     "url": "https://www.openstreetmap.org/copyright",
     "endpoints": ["https://overpass-api.de/api/interpreter",
                   "https://overpass.kumi.systems/api/interpreter"],
     "license": "ODbL-1.0", "license_url": "https://opendatacommons.org/licenses/odbl/1-0/",
     "attribution": "© OpenStreetMap contributors"},
]


def write_lines_json(path: Path, items: list[dict]) -> None:
    """A JSON array with one record per line: valid JSON that diffs well in git."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in items]
    path.write_text("[\n" + ",\n".join(lines) + "\n]\n" if lines else "[]\n")


def write_keyed_json(path: Path, mapping: dict) -> None:
    """A JSON object, one key per line, keys sorted."""
    lines = [f"{json.dumps(k)}:{json.dumps(mapping[k], ensure_ascii=False, separators=(',', ':'))}"
             for k in sorted(mapping)]
    path.write_text("{\n" + ",\n".join(lines) + "\n}\n")


def write_csv(path: Path, records: list[dict]) -> None:
    cols = [k for k in records[0] if k not in CSV_SKIP] if records else []
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(cols[:4] + ["alt_names", "bbox_south", "bbox_west", "bbox_north",
                                    "bbox_east"] + cols[4:])
        for r in records:
            bbox = r["bbox"] or [None] * 4
            row = [r[c] for c in cols[:4]] + ["; ".join(r["alt_names"] or [])] + bbox \
                + [r[c] for c in cols[4:]]
            writer.writerow(["" if v is None else v for v in row])


def write_shapes(pack_dir: Path, records: list[dict], extras: dict) -> None:
    for sub in SHAPE_DIRS:
        folder = pack_dir / sub
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("*.json"):
            old.unlink()
        grouped: dict[str, dict] = {}
        for rec in records:
            value = extras[sub].get(rec["id"])
            if value:
                grouped.setdefault(rec["state"] or "unknown", {})[rec["id"]] = value
        for state, mapping in grouped.items():
            write_keyed_json(folder / f"{state}.json", mapping)


def block_summary(records: list[dict], ran: set, statuses: dict, prev: dict | None,
                  run_date: str) -> dict:
    prev_blocks = (prev or {}).get("blocks") or {}
    out = {}
    for block in BLOCKS:
        pairs = sorted([r["id"], r["blocks"][block.name]["hash"]] for r in records
                       if r["blocks"].get(block.name))
        old = prev_blocks.get(block.name) or {}
        srcs = {}
        for src in block.sources:
            if src in statuses:
                srcs[src] = {k: v for k, v in statuses[src].items() if k not in ("source", "block")}
                srcs[src]["run_date"] = run_date
            elif (old.get("sources") or {}).get(src):
                srcs[src] = old["sources"][src]
        last = run_date if block.name in ran else old.get("last_run")
        out[block.name] = {
            "cadence_days": block.cadence_days,
            "last_run": last,
            "status": "never run" if not last else (
                "ok" if not any((s or {}).get("note") or (s or {}).get("errors")
                                for s in srcs.values()) else "ok with notes"),
            "beaches": len(pairs),
            "pack_hash": sha256(pairs) if pairs else None,
            "sources": srcs,
        }
    return out


def manifest(records: list[dict], extras: dict, run_date: str, report: dict,
             block_stats: dict, prev: dict | None, tile_ids: list[str]) -> dict:
    blocks = block_summary(records, extras["ran"], extras["statuses"], prev, run_date)
    sources = OSM_SOURCES + [{**MODULES[n].SOURCE, "block": MODULES[n].BLOCK} for n in MODULES]
    return {
        "name": "BeachX.pack",
        "pipeline_version": PIPELINE_VERSION,
        "run_date": run_date,
        "derive": DERIVE_KEY,
        "pack_hash": sha256([[b, v["pack_hash"]] for b, v in blocks.items()]),
        "attribution": "© OpenStreetMap contributors (ODbL-1.0). Wikidata facts CC0. "
                       "Wikipedia descriptions CC BY-SA 4.0 (source URL per beach). "
                       "NOAA, NWS, US Census, and USGS PAD-US data are US public domain.",
        "sources": sources,
        "blocks": blocks,
        "tiles": tile_ids,
        "counts": {
            "beaches": len(records),
            "by_tier": {t: sum(r["tier"] == t for r in records) for t in TIERS},
            "by_state": dict(sorted(Counter(r["state"] or "unknown" for r in records).items())),
            "by_country": dict(sorted(Counter(r["country"] for r in records).items())),
            "seed_space": sum(r["seed_space"] for r in records),
            "with_boundary": sum(r["has_boundary"] for r in records),
            "with_grid": sum(r["has_grid"] for r in records),
            "grid_cells": sum(r["cell_count"] or 0 for r in records),
            "with_webcam": sum(bool(r.get("has_webcam")) for r in records),
            "geometry_pipeline": dict(sorted(report.items())),
            "block_changes": dict(sorted(block_stats.items())),
        },
    }


def write_pack(pack_dir: Path, records: list[dict], extras: dict, man: dict) -> None:
    write_lines_json(pack_dir / "beaches.json", records)
    write_csv(pack_dir / "beaches.csv", records)
    write_shapes(pack_dir, records, extras)
    man["files"] = {"beaches": "beaches.json", "csv": "beaches.csv", **{
        sub: sorted(f"{sub}/{p.name}" for p in (pack_dir / sub).glob("*.json"))
        for sub in SHAPE_DIRS}}
    (pack_dir / "manifest.json").write_text(json.dumps(man, indent=2, ensure_ascii=False) + "\n")


def load_pack(pack_dir: Path) -> list[dict]:
    return json.loads((pack_dir / "beaches.json").read_text())


def load_manifest(pack_dir: Path) -> dict | None:
    path = pack_dir / "manifest.json"
    return json.loads(path.read_text()) if path.exists() else None


def load_shapes(pack_dir: Path, sub: str) -> dict:
    out: dict = {}
    for path in sorted((pack_dir / sub).glob("*.json")):
        out.update(json.loads(path.read_text()))
    return out


def select(records: list[dict], tier: str) -> list[dict]:
    return records if tier == "all" else [r for r in records if r["tier"] == tier]


def _dart_string(text: str) -> str:
    escaped = (text.replace("\\", "\\\\").replace("'", "\\'").replace("$", "\\$")
               .replace("\n", "\\n"))
    return f"'{escaped}'"


def to_dart(records: list[dict], run_date: str, class_name: str = "PackBeaches") -> str:
    year, month, day = (int(p) for p in run_date.split("-"))
    rows = "\n".join(
        f"      beach({_dart_string(r['id'])}, {_dart_string(r['name'])}, "
        f"{r['latitude']:.5f}, {r['longitude']:.5f}),"
        for r in records)
    return f"""// GENERATED by BeachX.pack (python3 -m beachx_pack export --dart). Do not edit.
// Data © OpenStreetMap contributors, ODbL-1.0. Pack run {run_date}.
import '../core/device_stamp.dart';
import '../models/beach.dart';

/// Catalog beaches from BeachX.pack. Missing rows are inserted. Existing rows stay.
abstract final class {class_name} {{
  static final stamp = DeviceStamp(
    deviceComputedAt: DateTime.utc({year}, {month}, {day}),
    deviceId: 'catalog',
    revision: 1,
    localOnly: false,
  );

  static List<Beach> beaches() {{
    Beach beach(String id, String name, double latitude, double longitude) {{
      return Beach(
        id: id,
        name: name,
        latitude: latitude,
        longitude: longitude,
        official: true,
        stamp: stamp,
      );
    }}

    return [
{rows}
    ];
  }}
}}
"""


def to_app_json(records: list[dict], pack_dir: Path, *, boundaries: bool, grids: bool,
                nearby: bool, run_date: str) -> dict:
    extra = {name: load_shapes(pack_dir, name) for name, on in
             (("boundaries", boundaries), ("grids", grids), ("nearby", nearby)) if on}
    key = {"boundaries": "boundary", "grids": "grid", "nearby": "nearby"}
    items = []
    for r in records:
        item = {k: r.get(k) for k in APP_FIELDS}
        for name, shapes in extra.items():
            item[key[name]] = shapes.get(r["id"])
        items.append(item)
    return {"run_date": run_date,
            "attribution": "© OpenStreetMap contributors (ODbL-1.0); descriptions from "
                           "Wikipedia (CC BY-SA 4.0)",
            "count": len(items), "beaches": items}
