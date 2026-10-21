"""NOAA CO-OPS tidal datums for each beach's tide station. Public domain.

One request per tide station (raw/<run-date>/datums/<station>.json). A
subordinate station publishes no datums; its reference (harmonic) station's
datums stand in, and `tide_datum_station_id` says which station answered.
Values are kept as differences, which do not depend on the station datum:
mean high water above mean sea level, mean high water above NAVD 88 (null
where NAVD 88 is not tied in), and the highest astronomical tide above mean
high water.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ..http import FetchError, request_json
from .common import now, write_json

NAME = "datums"
BLOCK = "stations"
URL = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/{id}/datums.json"
FIELDS = ("tide_datum_station_id", "tide_mhw_above_msl_m", "tide_mhw_above_navd88_m",
          "tide_hat_above_mhw_m")
SOURCE = {"name": "NOAA CO-OPS tidal datums", "url": URL.format(id="<station>"),
          "license": "Public domain (US government work)", "attribution": "NOAA CO-OPS"}
SLEEP = 0.3


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


def pull(raw_dir: Path, records: list[dict], opts: dict, log=print) -> dict:
    status = {"calls": 0, "ok": 0, "errors": 0, "cached": 0, "endpoint": URL, "note": None}
    from . import tides
    stations = _stations(raw_dir)
    near = tides.fresh(raw_dir, records) or {}
    wanted = sorted({v["tide_station_id"] for v in near.values() if v.get("tide_station_id")})
    status["eligible"] = len(wanted)
    for sid in wanted:
        try:
            data = _fetch(raw_dir, sid, opts.get("force", False), status, log)
            ref = (stations.get(sid) or {}).get("reference_id")
            if not values(data or {}) and ref:
                _fetch(raw_dir, ref, opts.get("force", False), status, log)
        except FetchError as err:
            status["errors"] += 1
            log(f"  {NAME}: {sid} failed: {err}")
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
        answer, v = sid, datums_of(sid)
        if v is None:
            ref = (stations.get(sid) or {}).get("reference_id")
            answer, v = ref, datums_of(ref) if ref else None
        out[r["id"]] = ({"tide_datum_station_id": answer, **differences(v)} if v
                        else dict.fromkeys(FIELDS))
    return out
