"""Shared plumbing for enrichment sources: per-point caching, status, nearest search."""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..hashing import sha256
from ..http import FetchError

UNREACHABLE_AFTER = 3  # consecutive failures before a source gives up for this run


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    tmp.replace(path)


def per_point(name: str, raw_dir: Path, targets: list[dict],
              fetch: Callable[[dict], tuple[str, dict]], *, limit: int | None, force: bool,
              sleep: float, log=print) -> dict:
    """Fetch one response per target beach into raw/<run-date>/<name>/<beach id>.json.

    Already-cached beaches are skipped (resume). Gives up with a note after
    UNREACHABLE_AFTER consecutive failures. Returns a status dict.
    """
    folder = raw_dir / name
    started = time.time()
    status = {"calls": 0, "ok": 0, "errors": 0, "cached": 0, "eligible": len(targets),
              "endpoint": None, "note": None}
    streak = 0
    for rec in targets:
        path = folder / f"{rec['id']}.json"
        if path.exists() and not force:
            status["cached"] += 1
            continue
        if limit is not None and status["calls"] >= limit:
            status["note"] = f"stopped at --limit {limit}"
            break
        status["calls"] += 1
        try:
            url, response = fetch(rec)
        except FetchError as err:
            status["errors"] += 1
            streak += 1
            log(f"  {name}: {rec['id']} failed: {err}")
            if streak >= UNREACHABLE_AFTER:
                status["note"] = f"unreachable after {streak} failures: {err}"
                break
            continue
        streak = 0
        status["ok"] += 1
        status["endpoint"] = status["endpoint"] or url.split("?")[0]
        write_json(path, {"beach_id": rec["id"], "url": url, "fetched_at": now(),
                          "response_hash": sha256(response), "response": response})
        time.sleep(sleep)
    status["seconds"] = round(time.time() - started, 1)
    log(f"  {name}: {status['calls']} calls, {status['ok']} ok, {status['errors']} errors, "
        f"{status['cached']} cached, {status['seconds']}s"
        + (f" ({status['note']})" if status["note"] else ""))
    return status


def load_points(raw_dir: Path, name: str) -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text()) for p in sorted((raw_dir / name).glob("*.json"))}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(
        math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(min(1.0, a)))


class Nearest:
    """Nearest-point lookup over a fixed list using a 1-degree bucket index.

    Rings of buckets are searched outward until the ring's minimum possible
    distance exceeds the best found, so the answer is exact.
    """

    def __init__(self, points: list[tuple[float, float, dict]]):
        self.buckets: dict[tuple[int, int], list] = {}
        for lat, lon, item in points:
            self.buckets.setdefault((math.floor(lat), math.floor(lon)), []).append((lat, lon, item))
        self.empty = not points

    def find(self, lat: float, lon: float, max_rings: int = 60) -> tuple[dict, float] | None:
        if self.empty:
            return None
        ci, cj = math.floor(lat), math.floor(lon)
        best: tuple[dict, float] | None = None
        for r in range(max_rings + 1):
            if best is not None:
                # anything in ring r is at least (r - 1) whole degrees of latitude away,
                # or (r - 1) degrees of longitude at the highest latitude in reach
                lat_gap = (r - 1) * 111.19
                lon_gap = (r - 1) * 111.19 * math.cos(math.radians(min(89.9, abs(lat) + r)))
                if min(lat_gap, lon_gap) > best[1]:
                    break
            for i in range(ci - r, ci + r + 1):
                for j in range(cj - r, cj + r + 1):
                    if max(abs(i - ci), abs(j - cj)) != r:
                        continue
                    jj = (j + 180) % 360 - 180
                    for plat, plon, item in self.buckets.get((i, jj), []):
                        d = haversine_km(lat, lon, plat, plon)
                        if best is None or d < best[1] or (d == best[1] and str(item) < str(best[0])):
                            best = (item, d)
        return best


def targets(records: list[dict], tiers: tuple[str, ...] = ("primary", "secondary")) -> list[dict]:
    """Beaches a per-point source looks up: the given tiers, best first."""
    chosen = [r for r in records if r["tier"] in tiers]
    return sorted(chosen, key=lambda r: (r["tier"] != "primary", -r["score"], r["id"]))
