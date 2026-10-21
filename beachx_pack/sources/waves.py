"""Typical wave height at each beach's NDBC buoy, for the wave margin.
Public domain.

One file per buoy within MAX_KM of a beach: the median significant wave
height (WVHT) over the last full calendar year of NDBC standard
meteorological history, falling back to the year before, then to the 45-day
real-time file. Each beach uses the nearest of those buoys that reports
waves (the nearest buoy overall may only measure weather). The wave margin
is half the median, clamped to [MARGIN_MIN, MARGIN_MAX] and rounded to
0.1 m. A beach with no such buoy within MAX_KM, or that is not on the sea or
a Great Lake, gets the documented default instead.
"""

from __future__ import annotations

import gzip
import json
import time
from pathlib import Path

from ..http import FetchError, request_json
from .common import now, write_json

NAME = "waves"
BLOCK = "stations"
HISTORY = "https://www.ndbc.noaa.gov/data/historical/stdmet/{id}h{year}.txt.gz"
REALTIME = "https://www.ndbc.noaa.gov/data/realtime2/{id}.txt"
FIELDS = ("wave_typical_m", "wave_margin_m", "wave_margin_source")
SOURCE = {"name": "NOAA NDBC standard meteorological data (WVHT)", "url": HISTORY,
          "license": "Public domain (US government work)", "attribution": "NOAA NDBC"}
MAX_KM = 100.0
MARGIN_SHARE = 0.5
MARGIN_MIN, MARGIN_MAX = 0.2, 2.0
DEFAULT_SEA_M = 0.5
DEFAULT_LAKE_M = 0.3
SLEEP = 0.5


def median_wvht(text: str) -> tuple[float | None, int]:
    """Median WVHT (metres) of an NDBC stdmet text file, and how many rows."""
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return None, 0
    head = lines[0].lstrip("#").split()
    if "WVHT" not in head:
        return None, 0
    col = head.index("WVHT")
    vals = []
    for line in lines[1:]:
        if line.startswith("#"):
            continue
        cells = line.split()
        if len(cells) > col and cells[col] not in ("MM", "99.0", "99.00"):
            try:
                v = float(cells[col])
            except ValueError:
                continue
            if 0 < v < 30:
                vals.append(v)
    if not vals:
        return None, 0
    vals.sort()
    return round(vals[len(vals) // 2], 2), len(vals)


def _text(url: str) -> str | None:
    import urllib.error
    import urllib.request
    from .. import USER_AGENT
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = resp.read()
            return (gzip.decompress(body) if url.endswith(".gz") else body).decode("ascii", "replace")
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            if err.code not in (429, 500, 502, 503, 504):
                raise FetchError(f"HTTP {err.code}") from err
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            pass
        time.sleep(5 * (attempt + 1))
    raise FetchError(f"failed: {url}")


def _buoys(raw_dir: Path) -> list[dict]:
    from . import buoys
    path = raw_dir / buoys.NAME / "station_table.json"
    return buoys.parse(json.loads(path.read_text())["response"]["text"]) if path.exists() else []


def candidates(raw_dir: Path, records: list[dict]) -> dict[str, list[tuple[float, str]]]:
    """Per beach, the buoys within MAX_KM, nearest first."""
    from .common import haversine_km
    table = _buoys(raw_dir)
    out = {}
    for r in records:
        lat, lng = r["latitude"], r["longitude"]
        near = []
        for b in table:
            if abs(b["lat"] - lat) > 1.0 or abs(b["lon"] - lng) > 1.2:
                continue
            d = haversine_km(lat, lng, b["lat"], b["lon"])
            if d <= MAX_KM:
                near.append((round(d, 2), b["id"]))
        out[r["id"]] = sorted(near)
    return out


def pull(raw_dir: Path, records: list[dict], opts: dict, log=print) -> dict:
    status = {"calls": 0, "ok": 0, "errors": 0, "cached": 0, "endpoint": HISTORY, "note": None}
    wanted = sorted({bid for near in candidates(raw_dir, records).values() for _, bid in near})
    status["eligible"] = len(wanted)
    year = int(raw_dir.name[:4]) if raw_dir.name[:4].isdigit() else 2026
    for bid in wanted:
        path = raw_dir / NAME / f"{bid}.json"
        if path.exists() and not opts.get("force"):
            status["cached"] += 1
            continue
        found = None
        try:
            for url in (HISTORY.format(id=bid.lower(), year=year - 1),
                        HISTORY.format(id=bid.lower(), year=year - 2),
                        REALTIME.format(id=bid.upper())):
                status["calls"] += 1
                text = _text(url)
                time.sleep(SLEEP)
                median, n = median_wvht(text) if text else (None, 0)
                if median is not None:
                    found = {"url": url, "median_m": median, "observations": n}
                    break
        except FetchError as err:
            status["errors"] += 1
            log(f"  {NAME}: {bid} failed: {err}")
            continue
        status["ok"] += 1
        write_json(path, {"buoy": bid, "fetched_at": now(), "result": found})
    log(f"  {NAME}: {len(wanted)} buoys within {MAX_KM:g} km, {status['calls']} calls, "
        f"{status['cached']} cached")
    return status


def margin(median: float) -> float:
    return round(min(MARGIN_MAX, max(MARGIN_MIN, MARGIN_SHARE * median)), 1)


def fresh(raw_dir: Path, records: list[dict]) -> dict[str, dict] | None:
    from . import buoys
    folder = raw_dir / NAME
    if not folder.is_dir() and not (raw_dir / buoys.NAME / "station_table.json").exists():
        return None  # the stations block was not pulled in this run
    # No folder with a buoy table means no beach had a buoy within MAX_KM (an
    # inland state): every beach still gets its default margin.
    results = {p.stem: json.loads(p.read_text())["result"] for p in folder.glob("*.json")}
    near = candidates(raw_dir, records)
    out = {}
    for r in records:
        kind = r.get("water_kind")
        on_sea = kind in (None, "sea") or "great_lakes" in (r.get("_regions") or ())
        result, bid = None, None
        if on_sea:
            for _, cand in near.get(r["id"], []):
                if results.get(cand):
                    result, bid = results[cand], cand
                    break
        if result:
            year = result["url"].rsplit("h", 1)[-1][:4] if "historical" in result["url"] else "45 days"
            out[r["id"]] = {"wave_typical_m": result["median_m"],
                            "wave_margin_m": margin(result["median_m"]),
                            "wave_margin_source": f"NDBC {bid} median WVHT {year}"}
        else:
            out[r["id"]] = {"wave_typical_m": None,
                            "wave_margin_m": DEFAULT_SEA_M if on_sea else DEFAULT_LAKE_M,
                            "wave_margin_source": "default"}
    return out
