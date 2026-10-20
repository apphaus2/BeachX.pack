"""Wikidata facts for OSM beaches that carry a `wikidata` tag. CC0."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from ..hashing import entity_hash
from ..http import request_json

NAME = "wikidata"
BLOCK = "wiki"
API = "https://www.wikidata.org/w/api.php"
FIELDS = ("wikidata_label", "wikidata_sitelinks", "wikidata_is_beach", "wikidata_is_park")
SOURCE = {"name": "Wikidata (wbgetentities)", "url": API, "license": "CC0 1.0",
          "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
          "attribution": "Wikidata"}
DEFAULT_LIMIT = 5000
BATCH = 50
SLEEP_BETWEEN_BATCHES = 1.0
QID = re.compile(r"^Q[1-9][0-9]*$")

BEACH_CLASSES = {
    "Q40080",     # beach
    "Q35800652",  # state beach
}
PARK_CLASSES = {
    "Q22698",     # park
    "Q22746",     # urban park
    "Q473972",    # protected area
    "Q46169",     # national park
    "Q179049",    # nature reserve
    "Q1761072",   # state park
    "Q5177940",   # county park
    "Q6063204",   # regional park
    "Q941043",    # United States National Seashore
    "Q1132998",   # National Recreation Area
    "Q113033132", # state recreation area
    "Q1410668",   # National Wildlife Refuge
    "Q1367500",   # marine protected area
}


def candidate_qids(records: list[dict], limit: int | None) -> list[str]:
    """QIDs of named primary and secondary beaches, sorted, capped. (A named
    beach with a wikidata tag already scores 6, so none are missed.)"""
    qids = {r["wikidata"] for r in records
            if r.get("name") and r.get("wikidata") and r["tier"] in ("primary", "secondary")}
    ordered = sorted(qids, key=lambda q: int(q[1:]))
    return ordered if limit is None else ordered[:limit]


def _trim(entity: dict) -> dict:
    """Keep only what the pack uses: label, sitelink keys, P31 classes."""
    if "missing" in entity:
        return {"id": entity.get("id"), "missing": True}
    p31 = []
    for claim in entity.get("claims", {}).get("P31", []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        if isinstance(value, dict) and value.get("id"):
            p31.append(value["id"])
    return {
        "id": entity.get("id"),
        # "mul" is Wikidata's default label for all languages; many items now
        # carry their English name only there.
        "label_en": (entity.get("labels", {}).get("en")
                     or entity.get("labels", {}).get("mul") or {}).get("value"),
        "sitelinks": sorted(entity.get("sitelinks", {})),
        "p31": p31,
    }


def load_cache(raw_dir: Path) -> dict[str, dict]:
    cache: dict[str, dict] = {}
    if not (raw_dir / "wikidata").is_dir():
        return cache
    for path in sorted((raw_dir / "wikidata").glob("batch_*.json")):
        cache.update(json.loads(path.read_text())["entities"])
    return cache


def pull(raw_dir: Path, records: list[dict], opts: dict, log=print) -> dict:
    """Fetch QIDs not already cached for this run (batches of 50)."""
    started = time.time()
    limit = opts.get("wikidata_limit", DEFAULT_LIMIT)
    if opts.get("limit") is not None:
        limit = min(limit, opts["limit"])
    qids = candidate_qids(records, limit)
    force = opts.get("force", False)
    folder = raw_dir / "wikidata"
    folder.mkdir(parents=True, exist_ok=True)
    if force:
        for old in folder.glob("batch_*.json"):
            old.unlink()
    cached = load_cache(raw_dir)
    todo = [q for q in qids if q not in cached]
    log(f"  wikidata: {len(qids)} QIDs wanted, {len(qids) - len(todo)} cached, "
        f"{len(todo)} to fetch")
    calls = 0
    next_no = len(list(folder.glob("batch_*.json"))) + 1
    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]
        calls += 1
        _, data = request_json([API], params={
            "action": "wbgetentities", "ids": "|".join(chunk), "format": "json",
            "props": "labels|sitelinks|claims", "languages": "en|mul", "maxlag": "5",
        }, timeout=120)
        entities = {}
        for qid, ent in data.get("entities", {}).items():
            entities[qid] = _trim(ent)
            source = ent.get("redirects", {}).get("from")
            if source:  # keep the QID the OSM tag used
                entities[source] = dict(entities[qid])
        for qid in chunk:  # a request id that came back under another key
            entities.setdefault(qid, {"id": qid, "missing": True})
        for qid, ent in entities.items():
            ent["source_hash"] = entity_hash(ent)
        path = folder / f"batch_{next_no:04d}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "requested": chunk, "entities": entities}, ensure_ascii=False, indent=1))
        tmp.replace(path)
        next_no += 1
        log(f"  wikidata: batch {start // BATCH + 1}/{-(-len(todo) // BATCH)} "
            f"({len(chunk)} ids)")
        time.sleep(SLEEP_BETWEEN_BATCHES)
    return {"calls": calls, "ok": calls, "errors": 0, "cached": len(qids) - len(todo),
            "eligible": len(qids), "endpoint": API, "seconds": round(time.time() - started, 1),
            "note": f"capped at {limit} QIDs" if limit is not None and len(qids) >= limit else None}


def fresh(raw_dir: Path, records: list[dict]) -> dict[str, dict] | None:
    cache = load_cache(raw_dir)
    if not cache:
        return None
    return {r["id"]: facts(cache[r["wikidata"]]) for r in records
            if r.get("wikidata") and r["wikidata"] in cache}


def facts(entity: dict | None) -> dict:
    """Fields merged into a beach record. All null when not fetched."""
    if not entity or entity.get("missing"):
        return {"wikidata_label": None, "wikidata_sitelinks": None,
                "wikidata_is_beach": None, "wikidata_is_park": None}
    classes = set(entity.get("p31", []))
    return {
        "wikidata_label": entity.get("label_en"),
        "wikidata_sitelinks": len(entity.get("sitelinks", [])),
        "wikidata_is_beach": bool(classes & BEACH_CLASSES),
        "wikidata_is_park": bool(classes & PARK_CLASSES),
    }
