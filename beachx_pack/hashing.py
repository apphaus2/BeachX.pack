"""Canonical SHA-256 hashes for OSM elements, Wikidata entities, and packs."""

from __future__ import annotations

import hashlib
import json


def _coord(value: float) -> str:
    return f"{value:.7f}"


def _points(geom: list | None) -> list:
    return [[_coord(g["lat"]), _coord(g["lon"])] if g else None for g in geom or []]


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256(obj) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def element_hash(el: dict) -> str:
    """Type, id, sorted tags, and geometry (node point, way points, relation
    members with role and points). Floats are fixed to 7 decimals."""
    body: dict = {"type": el["type"], "id": el["id"],
                  "tags": sorted((el.get("tags") or {}).items())}
    if el["type"] == "node":
        body["point"] = [_coord(el["lat"]), _coord(el["lon"])]
    elif el["type"] == "way":
        body["geometry"] = _points(el.get("geometry"))
    else:
        body["members"] = [[m.get("type"), m.get("ref"), m.get("role"),
                            _points(m.get("geometry"))] for m in el.get("members", [])]
    return sha256(body)


def entity_hash(entity: dict) -> str:
    """Hash of a trimmed Wikidata entity (without its own hash field)."""
    return sha256({k: v for k, v in entity.items() if k != "source_hash"})


def pack_hash(records: list[dict]) -> str:
    """One string per pack: SHA-256 over sorted (id, source_hash) pairs."""
    return sha256(sorted([r["id"], r["source_hash"]] for r in records))
