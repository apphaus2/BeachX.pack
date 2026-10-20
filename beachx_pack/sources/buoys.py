"""NOAA NDBC stations: nearest buoy for every beach. Public domain."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..hashing import sha256
from ..http import request_json
from .common import Nearest, now, write_json

NAME = "buoys"
BLOCK = "stations"
URL = "https://www.ndbc.noaa.gov/data/stations/station_table.txt"
FIELDS = ("wave_buoy_id", "wave_buoy_name", "wave_buoy_distance_km")
SOURCE = {"name": "NOAA NDBC station table", "url": URL,
          "license": "Public domain (US government work)", "attribution": "NOAA NDBC"}
# Station types kept: anything whose TTYPE says "buoy", except types that do not
# sit still or do not measure surface conditions.
EXCLUDE_TYPES = ("dart", "drifting", "atlas", "tsunami")
LOCATION = re.compile(r"^\s*([0-9.]+)\s+([NS])\s+([0-9.]+)\s+([EW])")


def parse(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cols = [c.strip() for c in line.split("|")]
        if len(cols) < 7:
            continue
        ttype = cols[2]
        low = ttype.lower()
        if "buoy" not in low or any(x in low for x in EXCLUDE_TYPES):
            continue
        m = LOCATION.match(cols[6])
        if not m:
            continue
        lat = float(m.group(1)) * (1 if m.group(2) == "N" else -1)
        lon = float(m.group(3)) * (1 if m.group(4) == "E" else -1)
        out.append({"id": cols[0], "name": cols[4], "type": ttype, "lat": lat, "lon": lon})
    return out


def pull(raw_dir: Path, records: list[dict], opts: dict, log=print) -> dict:
    path = raw_dir / NAME / "station_table.json"
    if path.exists() and not opts.get("force"):
        log(f"  {NAME}: cached")
        return {"calls": 0, "ok": 0, "errors": 0, "cached": 1, "endpoint": URL, "note": None}
    _, data = request_json([URL], timeout=120, raw_text=True)
    write_json(path, {"url": URL, "fetched_at": now(), "response_hash": sha256(data),
                      "response": data})
    log(f"  {NAME}: {len(parse(data['text']))} buoys kept")
    return {"calls": 1, "ok": 1, "errors": 0, "cached": 0, "endpoint": URL, "note": None}


def fresh(raw_dir: Path, records: list[dict]) -> dict[str, dict] | None:
    path = raw_dir / NAME / "station_table.json"
    if not path.exists():
        return None
    buoys = parse(json.loads(path.read_text())["response"]["text"])
    index = Nearest([(b["lat"], b["lon"], {"id": b["id"], "name": b["name"]}) for b in buoys])
    out = {}
    for r in records:
        hit = index.find(r["latitude"], r["longitude"])
        out[r["id"]] = ({"wave_buoy_id": hit[0]["id"], "wave_buoy_name": hit[0]["name"],
                         "wave_buoy_distance_km": round(hit[1], 2)} if hit else
                        dict.fromkeys(FIELDS))
    return out
