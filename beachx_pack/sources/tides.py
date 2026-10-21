"""NOAA CO-OPS tide prediction stations: nearest station for every beach. Public domain."""

from __future__ import annotations

from pathlib import Path

from ..hashing import sha256
from ..http import request_json
from .common import Nearest, now, write_json

NAME = "tides"
BLOCK = "stations"
URL = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json?type=tidepredictions"
FIELDS = ("tide_station_id", "tide_station_name", "tide_station_distance_km")
SOURCE = {"name": "NOAA CO-OPS tide prediction stations", "url": URL,
          "license": "Public domain (US government work)", "attribution": "NOAA CO-OPS"}


def pull(raw_dir: Path, records: list[dict], opts: dict, log=print) -> dict:
    path = raw_dir / NAME / "stations.json"
    if path.exists() and not opts.get("force"):
        log(f"  {NAME}: cached")
        return {"calls": 0, "ok": 0, "errors": 0, "cached": 1, "endpoint": URL, "note": None}
    _, data = request_json([URL], timeout=120)
    write_json(path, {"url": URL, "fetched_at": now(), "response_hash": sha256(data),
                      "response": data})
    log(f"  {NAME}: {len(data.get('stations', []))} stations")
    return {"calls": 1, "ok": 1, "errors": 0, "cached": 0, "endpoint": URL, "note": None}


def fresh(raw_dir: Path, records: list[dict]) -> dict[str, dict] | None:
    path = raw_dir / NAME / "stations.json"
    if not path.exists():
        return None
    import json
    stations = json.loads(path.read_text())["response"].get("stations", [])
    index = Nearest([(s["lat"], s["lng"], {"id": s["id"], "name": s.get("name")})
                     for s in stations if s.get("lat") is not None and s.get("lng") is not None])
    out = {}
    for r in records:
        if r.get("water_kind") in ("lake", "river"):  # no tide on a lake or a river
            out[r["id"]] = dict.fromkeys(FIELDS)
            continue
        hit = index.find(r["latitude"], r["longitude"])
        out[r["id"]] = ({"tide_station_id": hit[0]["id"], "tide_station_name": hit[0]["name"],
                         "tide_station_distance_km": round(hit[1], 2)} if hit else
                        dict.fromkeys(FIELDS))
    return out
