"""NOAA CO-OPS tidal datums for each beach's tide station. Public domain.

One request per tide station (raw/<run-date>/datums/<station>.json). A
subordinate station publishes no datums; its reference (harmonic) station's
datums stand in, and `tide_datum_station_id` says which station answered.
Values are kept as differences, which do not depend on the station datum:
mean high water above mean sea level, mean high water above NAVD 88, and
the highest astronomical tide above mean high water.

Many stations publish only some of these (on the California coast, a third
of them have no NAVD 88 tie or no HAT). A difference the station does not
publish is taken from the nearest station within FILL_KM of it that does
(its own datums, or its reference station's), asking at most FILL_TRIES
neighbours, and the station that answered each one is named:
`tide_datum_station_id` (mean sea level), `tide_navd88_station_id`, and
`tide_hat_station_id`. Without one the difference stays null. Reason:
mainland lidar is NAVD 88, so a beach whose station has no NAVD 88 tie would
otherwise be read as if NAVD 88 were mean sea level, 0.8 m off in Southern
California.
"""

from __future__ import annotations

import itertools
import json
import time
from pathlib import Path

from ..http import FetchError, request_json
from .common import now, write_json

NAME = "datums"
BLOCK = "stations"
URL = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/{id}/datums.json"
FIELDS = ("tide_datum_station_id", "tide_mhw_above_msl_m", "tide_mhw_above_navd88_m",
          "tide_hat_above_mhw_m", "tide_navd88_station_id", "tide_hat_station_id")
SOURCE = {"name": "NOAA CO-OPS tidal datums", "url": URL.format(id="<station>"),
          "license": "Public domain (US government work)", "attribution": "NOAA CO-OPS"}
SLEEP = 0.3
FILL_KM = 50.0
FILL_TRIES = 10
# difference field -> (datum it needs besides MHW, field naming the station that gave it)
PARTS = {"tide_mhw_above_msl_m": ("MSL", "tide_datum_station_id"),
         "tide_mhw_above_navd88_m": ("NAVD88", "tide_navd88_station_id"),
         "tide_hat_above_mhw_m": ("HAT", "tide_hat_station_id")}


def values(response: dict) -> dict[str, float] | None:
    """{name: metres} with HAT and LAT folded in, or None when the station
    publishes no datums."""
    rows = response.get("datums") or []
    out = {d["name"]: d["value"] for d in rows if d.get("value") is not None}
    for key in ("HAT", "LAT"):
        if response.get(key) is not None:
            out[key] = response[key]
    return out if "MHW" in out else None


def differences(v: dict) -> dict:
    return {
        "tide_mhw_above_msl_m": round(v["MHW"] - v["MSL"], 3) if "MSL" in v else None,
        "tide_mhw_above_navd88_m": round(v["MHW"] - v["NAVD88"], 3) if "NAVD88" in v else None,
        "tide_hat_above_mhw_m": round(v["HAT"] - v["MHW"], 3) if "HAT" in v else None,
    }


def _stations(raw_dir: Path) -> dict[str, dict]:
    path = raw_dir / "tides" / "stations.json"
    if not path.exists():
        return {}
    return {s["id"]: s for s in json.loads(path.read_text())["response"].get("stations", [])}


def _fetch(raw_dir: Path, sid: str, force: bool, status: dict, log) -> dict | None:
    path = raw_dir / NAME / f"{sid}.json"
    if path.exists() and not force:
        status["cached"] += 1
        return json.loads(path.read_text())["response"]
    status["calls"] += 1
    url, data = request_json([URL.format(id=sid)], params={"units": "metric"}, timeout=60,
                             attempts=3, backoff=5, accept_status=frozenset({404}), log=log)
    write_json(path, {"station": sid, "url": url, "fetched_at": now(), "response": data})
    status["ok"] += 1
    time.sleep(SLEEP)
    return data


def neighbours(stations: dict[str, dict], sid: str) -> list[str]:
    """Other stations within FILL_KM of `sid`, nearest first, at most FILL_TRIES."""
    from .common import haversine_km
    me = stations.get(sid)
    if not me or me.get("lat") is None:
        return []
    near = []
    for oid, o in stations.items():
        if oid == sid or o.get("lat") is None or o.get("lng") is None:
            continue
        d = haversine_km(me["lat"], me["lng"], o["lat"], o["lng"])
        if d <= FILL_KM:
            near.append((d, oid))
    return [oid for _, oid in sorted(near)[:FILL_TRIES]]


def fill(sid: str, stations: dict[str, dict], datums_of) -> dict:
    """The beach's datum fields for tide station `sid`. `datums_of(id)` gives a
    station's values or None. Each difference comes from the station (or its
    reference station when it publishes nothing), else from the nearest
    neighbour that has it."""
    def own(oid: str) -> tuple[str | None, dict | None]:
        v = datums_of(oid)
        if v is None:
            ref = (stations.get(oid) or {}).get("reference_id")
            if ref:
                return ref, datums_of(ref)
        return oid, v

    out = dict.fromkeys(FIELDS)
    first, v = own(sid)
    want = list(PARTS)
    # neighbours are asked lazily, nearest first, only while a difference is missing
    for oid, vals in itertools.chain([(first, v)], (own(n) for n in neighbours(stations, sid))):
        if not vals:
            continue
        diff = differences(vals)
        for field in list(want):
            if diff[field] is not None:
                out[field] = diff[field]
                out[PARTS[field][1]] = oid
                want.remove(field)
        if not want:
            break
    return out


def pull(raw_dir: Path, records: list[dict], opts: dict, log=print) -> dict:
    status = {"calls": 0, "ok": 0, "errors": 0, "cached": 0, "endpoint": URL, "note": None}
    from . import tides
    stations = _stations(raw_dir)
    near = tides.fresh(raw_dir, records) or {}
    wanted = sorted({v["tide_station_id"] for v in near.values() if v.get("tide_station_id")})
    status["eligible"] = len(wanted)
    force = opts.get("force", False)
    seen: dict[str, dict | None] = {}

    def datums_of(sid: str) -> dict | None:
        if sid not in seen:
            try:
                seen[sid] = values(_fetch(raw_dir, sid, force, status, log) or {})
            except FetchError as err:
                status["errors"] += 1
                log(f"  {NAME}: {sid} failed: {err}")
                seen[sid] = None
        return seen[sid]

    for sid in wanted:
        fill(sid, stations, datums_of)  # fetches the station, its reference, and neighbours
    log(f"  {NAME}: {len(wanted)} stations, {status['calls']} calls, {status['cached']} cached")
    return status


def fresh(raw_dir: Path, records: list[dict]) -> dict[str, dict] | None:
    folder = raw_dir / NAME
    if not folder.is_dir():
        return None
    from . import tides
    stations = _stations(raw_dir)
    near = tides.fresh(raw_dir, records) or {}
    cache: dict[str, dict | None] = {}
    answers: dict[str, dict] = {}

    def datums_of(sid: str) -> dict | None:
        if sid not in cache:
            path = folder / f"{sid}.json"
            cache[sid] = values(json.loads(path.read_text())["response"]) if path.exists() else None
        return cache[sid]

    out = {}
    for r in records:
        sid = (near.get(r["id"]) or {}).get("tide_station_id")
        if not sid:
            out[r["id"]] = dict.fromkeys(FIELDS)
            continue
        if sid not in answers:
            answers[sid] = fill(sid, stations, datums_of)
        out[r["id"]] = dict(answers[sid])
    return out
