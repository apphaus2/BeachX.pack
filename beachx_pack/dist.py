"""dist: one state's committed JSON pack -> dist/<country>/<state>.sqlite.gz.

The phone downloads the gzipped SQLite file, unzips it once, and opens it
read-only. dist/ mirrors the Pages root (`index.json`, `us/hi.sqlite.gz`)
and is git-ignored: the JSON under pack/ is the reviewed source. The same
pack gives the same bytes (rows inserted in id order, VACUUM, gzip with no
name and mtime 0), so the sha256 in the index only changes with the pack.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import cells
from .export import load_manifest, load_pack, load_shapes

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE manifest (
  country TEXT NOT NULL, state TEXT NOT NULL, run_date TEXT NOT NULL, pack_hash TEXT NOT NULL,
  cell_record_version INTEGER NOT NULL, schema_version INTEGER NOT NULL,
  attribution TEXT NOT NULL, json TEXT NOT NULL);
CREATE TABLE beaches (
  id TEXT PRIMARY KEY, name TEXT, display_name TEXT NOT NULL,
  latitude REAL NOT NULL, longitude REAL NOT NULL, state TEXT, country TEXT,
  tier TEXT NOT NULL, score INTEGER NOT NULL, seed_space INTEGER NOT NULL,
  surface TEXT, access TEXT, lifeguard TEXT, water_kind TEXT, orientation TEXT,
  area_m2 REAL, cell_count INTEGER, timezone TEXT,
  tide_station_id TEXT, wave_buoy_id TEXT, datum_offset_m REAL, wet_limit_m REAL,
  wave_margin_m REAL, osm_type TEXT NOT NULL, osm_id INTEGER NOT NULL, osm_url TEXT NOT NULL,
  wikidata TEXT, wikipedia_url TEXT, description TEXT, description_source_url TEXT,
  json TEXT NOT NULL);
CREATE INDEX beaches_latitude_longitude ON beaches (latitude, longitude);
CREATE TABLE boundaries (
  beach_id TEXT PRIMARY KEY, rings TEXT NOT NULL, holes TEXT, parts TEXT, water_edge TEXT);
CREATE TABLE grids (
  beach_id TEXT PRIMARY KEY, anchor_latitude REAL NOT NULL, anchor_longitude REAL NOT NULL,
  heading_degrees REAL NOT NULL, cell_size_m REAL NOT NULL, cell_count INTEGER NOT NULL,
  x_min INTEGER NOT NULL, x_max INTEGER NOT NULL, y_min INTEGER NOT NULL, y_max INTEGER NOT NULL,
  cell_record_version INTEGER NOT NULL, rows BLOB NOT NULL);
CREATE TABLE nearby (
  beach_id TEXT NOT NULL, kind TEXT NOT NULL, osm_type TEXT NOT NULL, osm_id INTEGER NOT NULL,
  name TEXT, latitude REAL NOT NULL, longitude REAL NOT NULL, distance_m REAL,
  website TEXT, webcam_url TEXT, tags TEXT NOT NULL);
CREATE INDEX nearby_beach_id ON nearby (beach_id);
"""
BEACH_COLUMNS = ("id", "name", "display_name", "latitude", "longitude", "state", "country", "tier",
                 "score", "seed_space", "surface", "access", "lifeguard", "water_kind",
                 "orientation", "area_m2", "cell_count", "timezone", "tide_station_id",
                 "wave_buoy_id", "datum_offset_m", "wet_limit_m", "wave_margin_m", "osm_type",
                 "osm_id", "osm_url", "wikidata", "wikipedia_url", "description",
                 "description_source_url")


def _json(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def write_sqlite(pack_dir: Path, path: Path) -> dict:
    """The state pack as one SQLite file. Returns its counts."""
    man = load_manifest(pack_dir)
    records = sorted(load_pack(pack_dir), key=lambda r: r["id"])
    boundaries = load_shapes(pack_dir, "boundaries")
    grids = load_shapes(pack_dir, "grids")
    nearby = load_shapes(pack_dir, "nearby")
    if path.exists():
        path.unlink()
    db = sqlite3.connect(path)
    db.execute("PRAGMA page_size = 4096")
    db.execute("PRAGMA journal_mode = OFF")
    db.executescript(SCHEMA)
    db.execute("INSERT INTO manifest VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
        man["country"].lower(), man["state"].lower(), man["run_date"], man["pack_hash"],
        man["cell_record_version"], SCHEMA_VERSION, man["attribution"], _json(man)))
    marks = ",".join("?" * (len(BEACH_COLUMNS) + 1))
    db.executemany(f"INSERT INTO beaches VALUES ({marks})", [
        tuple(int(r[c]) if c == "seed_space" else r.get(c) for c in BEACH_COLUMNS) + (_json(r),)
        for r in records])
    ids = [r["id"] for r in records]
    db.executemany("INSERT INTO boundaries VALUES (?, ?, ?, ?, ?)", [
        (rid, _json(b["rings"]), _json(b.get("holes")), _json(b.get("parts")),
         _json(b.get("water_edge"))) for rid in ids if (b := boundaries.get(rid))])
    rows = []
    for rid in ids:
        g = grids.get(rid)
        if not g:
            continue
        a = g["anchor"]
        rows.append((rid, a["latitude"], a["longitude"], a["heading_degrees"], g["cell_size_m"],
                     g["cell_count"], g["x_min"], g["x_max"], g["y_min"], g["y_max"],
                     g["cell_record_version"], cells.unb64(g["rows"])))
    db.executemany(f"INSERT INTO grids VALUES ({','.join('?' * 12)})", rows)
    db.executemany(f"INSERT INTO nearby VALUES ({','.join('?' * 11)})", [
        (rid, it["kind"], it["osm_type"], it["osm_id"], it.get("name"), it["latitude"],
         it["longitude"], it.get("distance_m"), it.get("website"), it.get("webcam_url"),
         _json(it.get("tags") or {})) for rid in ids for it in nearby.get(rid, [])])
    db.commit()
    db.execute("VACUUM")
    db.close()
    return {"beaches": len(records), "grids": len(rows), "cells": sum(r[5] for r in rows),
            "manifest": man}


def gzip_file(src: Path, dst: Path) -> None:
    tmp = dst.with_name(f"{dst.name}.{os.getpid()}.tmp")
    with open(src, "rb") as fin, open(tmp, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=raw, mtime=0) as gz:
            while chunk := fin.read(1 << 20):
                gz.write(chunk)
    tmp.replace(dst)


def build(pack_dir: Path, dist_root: Path) -> dict:
    """dist/<country>/<state>.sqlite.gz and its entry in dist/index.json."""
    man = load_manifest(pack_dir)
    country, state = man["country"].lower(), man["state"].lower()
    out = dist_root / country / f"{state}.sqlite.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    plain = out.with_name(f"{state}.sqlite")
    counts = write_sqlite(pack_dir, plain)
    gzip_file(plain, out)
    raw_size = plain.stat().st_size
    plain.unlink()
    data = out.read_bytes()
    entry = {"country": country, "state": state, "path": f"{country}/{state}.sqlite.gz",
             "size": len(data), "sqlite_size": raw_size,
             "sha256": hashlib.sha256(data).hexdigest(), "pack_hash": man["pack_hash"],
             "beaches": counts["beaches"], "grids": counts["grids"], "cells": counts["cells"],
             "cell_record_version": man["cell_record_version"], "schema_version": SCHEMA_VERSION,
             "run_date": man["run_date"],
             "built": datetime.now(timezone.utc).date().isoformat()}
    update_index(dist_root, entry)
    return entry


def update_index(dist_root: Path, entry: dict) -> dict:
    path = dist_root / "index.json"
    index = json.loads(path.read_text()) if path.exists() else {}
    entries = {(e["country"], e["state"]): e for e in index.get("states", [])}
    entries[(entry["country"], entry["state"])] = entry
    out = {"name": "BeachX.pack", "attribution": "© OpenStreetMap contributors (ODbL-1.0)",
           "states": [entries[k] for k in sorted(entries)]}
    dist_root.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"index.json.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)
    return out
