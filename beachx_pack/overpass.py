"""Overpass pull: one cached JSON file per tile, resumable."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .hashing import element_hash
from .http import FetchError, NoAreas, request_json
from .tiles import COUNTRY_AREA, Tile, states_for_tile

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",  # volunteer server, lower limits
    "https://overpass.kumi.systems/api/interpreter",
]
SLEEP_BETWEEN_TILES = 5.0
MARKER_TYPE = "beachx_area"
# Area canary: Waikiki Beach (way 218679862) must test as inside the US area.
# A server whose area filter silently drops ways answers 0 here.
CANARY_WAY = 218679862


def build_query(tile: Tile, areas: bool = True) -> str:
    """Beaches in the tile with full geometry, then area membership markers.

    Each `make beachx_area` element lists which of the tile's beaches lie in
    one area (the country, then each candidate state). Without `areas` (a
    server with no area database) the markers are replaced by a count of the
    beaches, which check_count compares with what came back.
    """
    s, w, n, e = tile.bbox
    box = f"({s},{w},{n},{e})"
    lines = [
        "[out:json][timeout:300];",
        "(",
        f'  nwr["natural"="beach"]{box};',
        f'  nwr["leisure"="beach_resort"]{box};',
        ")->.b;",
        ".b out meta geom qt;",
    ]
    if not areas:
        return "\n".join(lines + [".b out count;"]) + "\n"
    found = [(tile.country, COUNTRY_AREA[tile.country])]
    if tile.country == "US":
        found += [(f"US-{st}", f'["ISO3166-2"="US-{st}"]') for st in states_for_tile(tile)]
    for i, (code, flt) in enumerate(found):
        lines += [
            f"area{flt}->.a{i};",
            f"nwr.b(area.a{i});",
            f'make {MARKER_TYPE} code="{code}", area_ids=a{i}.set(id()), '
            'members=set(type()+"/"+id());',
            "out;",
        ]
        if code == "US":
            lines += [f"way({CANARY_WAY})(area.a{i});", "out count;"]
    return "\n".join(lines) + "\n"


KINDS = ("osm", "osm_nearby", "osm_water")  # beaches, nearby things, water near beaches
KIND_BLOCK = {"osm": "geometry", "osm_nearby": "nearby", "osm_water": "geometry"}


def tile_path(raw_dir: Path, tile: Tile, kind: str = "osm") -> Path:
    return raw_dir / kind / f"{tile.id}.json"


AREA_RECHECKS = 2  # extra fetches when a beach answer looks wrong


def check_count(response: dict) -> None:
    """For a query without areas: the beaches that came back must match the
    server's own count of them (a cut-off answer fails)."""
    els = response.get("elements", [])
    counts = [e for e in els if e.get("type") == "count"]
    got = sum(1 for e in els if e.get("type") in ("node", "way", "relation"))
    if len(counts) != 1:
        raise FetchError("beach response is missing its count")
    if int(counts[0]["tags"]["total"]) != got:
        raise FetchError(f"beach response has {got} of {counts[0]['tags']['total']} beaches")


def check_areas(response: dict) -> None:
    """Raise when the country area resolved but contains none of the tile's
    beaches. That is what a silent server-side area failure looks like, but a
    tile whose only mapped beaches are foreign looks the same, so fetch_tile
    re-asks a few times and then keeps the answer marked `suspect`."""
    beaches = sum(1 for e in response.get("elements", []) if e.get("type") in ("node", "way", "relation"))
    markers = [e for e in response.get("elements", []) if e.get("type") == MARKER_TYPE]
    if not markers:
        raise FetchError("beach response is missing its area markers")
    country = markers[0]["tags"]
    canary = [e for e in response.get("elements", []) if e.get("type") == "count"]
    if country.get("code") == "US" and canary and int(canary[0]["tags"]["total"]) < 1:
        raise FetchError("area canary failed: Waikiki Beach not inside the US area (server-side)")
    if beaches and country.get("area_ids") and not country.get("members"):
        raise FetchError(f"country area matched 0 of {beaches} beaches (server-side); retrying")


def query_for(tile: Tile, kind: str) -> str:
    if kind == "osm":
        return build_query(tile)
    if kind == "osm_water":
        from .water import build_query as water_query
        return water_query(tile.bbox)
    from .nearby import build_query as nearby_query
    return nearby_query(tile.bbox)


def summarize(payload: dict, kind: str = "osm") -> str:
    els = payload["response"]["elements"]
    if kind == "osm_water":
        from .water import kind_of as water_kind
        found: dict[str, int] = {}
        for el in els:
            if el["type"] in ("way", "relation"):
                k = water_kind(el.get("tags", {})) or "other"
                found[k] = found.get(k, 0) + 1
        return f"{sum(found.values()):5d} water ({', '.join(f'{k} {v}' for k, v in sorted(found.items()))})"
    if kind == "osm_nearby":
        from .nearby import kind_of
        found: dict[str, int] = {}
        els = [e for e in els if e["type"] != "count"]
        for el in els:
            k = kind_of(el.get("tags", {})) or "other"
            found[k] = found.get(k, 0) + 1
        return f"{len(els):5d} nearby ({', '.join(f'{k} {v}' for k, v in sorted(found.items()))})"
    kinds = {"node": 0, "way": 0, "relation": 0}
    areas = []
    els = [e for e in els if e["type"] != "count"]
    for el in els:
        if el["type"] in kinds:
            kinds[el["type"]] += 1
        elif el["type"] == MARKER_TYPE:
            members = el["tags"].get("members", "")
            count = len(members.split(";")) if members else 0
            found_area = "" if el["tags"].get("area_ids") else "?"
            areas.append(f"{el['tags']['code']}{found_area}:{count}")
    total = sum(kinds.values())
    return (f"{total:5d} elements (n {kinds['node']}, w {kinds['way']}, r {kinds['relation']})"
            f"  in {' '.join(areas) or '(no areas: count checked)'}")


def fetch_tile(tile: Tile, raw_dir: Path, endpoints: list[str], kind: str = "osm",
               log=print) -> dict:
    query = query_for(tile, kind)
    started = time.time()
    suspect = None
    if kind == "osm":
        for attempt in range(AREA_RECHECKS + 1):
            try:
                url, response = request_json(endpoints, data={"data": query}, log=log)
            except NoAreas as err:
                # no endpoint could answer the area checks: ask without them
                log(f"    {tile.id}: {err}; asking without areas")
                query = build_query(tile, areas=False)
                url, response = request_json(endpoints, data={"data": query},
                                             validate=check_count, log=log)
                suspect = None
                break
            try:
                check_areas(response)
                suspect = None
                break
            except FetchError as err:
                suspect = str(err)
                log(f"    {tile.id}: {suspect} (check {attempt + 1}/{AREA_RECHECKS + 1})")
                time.sleep(SLEEP_BETWEEN_TILES * 4)
    else:
        from .nearby import check_canary
        if tile_path(raw_dir, tile, "osm").exists():  # asked by box, cell by cell (subbox.py)
            from dataclasses import replace
            from . import subbox
            url, response, _ = subbox.fetch(
                tile, raw_dir, endpoints, kind,
                lambda b: query_for(replace(tile, south=b[0], west=b[1], north=b[2], east=b[3]),
                                    kind), log=log)
        else:
            url, response = request_json(endpoints, data={"data": query},
                                         validate=check_canary, log=log)
    for el in response.get("elements", []):
        if el.get("type") in ("node", "way", "relation"):
            el["source_hash"] = element_hash(el)
    payload = {
        "tile": {"id": tile.id, "bbox": list(tile.bbox), "state": tile.state,
                 "country": tile.country, "region": tile.region},
        "kind": kind,
        "suspect": suspect,
        "endpoint": url,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(time.time() - started, 1),
        "query": query,
        "response": response,
    }
    write_payload(tile_path(raw_dir, tile, kind), payload)
    return payload


def write_payload(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False))
    tmp.replace(path)  # atomic: a killed run never leaves a half tile


def beach_count(raw_dir: Path, tile: Tile) -> int | None:
    """Beaches in the tile's cached beach answer, None when it is not on disk."""
    path = tile_path(raw_dir, tile, "osm")
    if not path.exists():
        return None
    els = json.loads(path.read_text())["response"].get("elements", [])
    return sum(1 for e in els if e.get("type") in ("node", "way", "relation"))


def empty_payload(tile: Tile, kind: str) -> dict:
    """A nearby or water answer for a tile with no beaches, written without
    asking Overpass (most generated inland tiles at a state's edge)."""
    return {"tile": {"id": tile.id, "bbox": list(tile.bbox), "state": tile.state,
                     "country": tile.country, "region": tile.region},
            "kind": kind, "suspect": None, "endpoint": None, "empty": "no beaches in the tile",
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "seconds": 0.0, "query": query_for(tile, kind), "response": {"elements": []}}


def pull_tiles(tiles: list[Tile], raw_dir: Path, *, kinds: tuple[str, ...] = KINDS,
               force: bool = False, endpoints: list[str] | None = None,
               log=print) -> dict[str, dict]:
    """Fetch every tile query not already cached. Returns a status per kind."""
    endpoints = endpoints or ENDPOINTS
    status = {k: {"calls": 0, "ok": 0, "errors": 0, "cached": 0, "eligible": len(tiles),
                  "endpoint": None, "note": None, "seconds": 0.0, "failed_tiles": []}
              for k in kinds}
    fetched_any = False
    width = len(str(len(tiles)))
    for i, tile in enumerate(tiles, 1):
        for kind in kinds:
            st = status[kind]
            path = tile_path(raw_dir, tile, kind)
            tag = {"osm": "beaches", "osm_nearby": "nearby ", "osm_water": "water  "}[kind]
            prefix = f"[{i:{width}d}/{len(tiles)}] {tile.id:<18} {tag}"
            if path.exists() and not force:
                st["cached"] += 1
                log(f"{prefix} cached   {summarize(json.loads(path.read_text()), kind)}")
                continue
            if kind != "osm" and beach_count(raw_dir, tile) == 0:
                # nearby and water both start from the tile's beaches: none, nothing to ask
                write_payload(path, empty_payload(tile, kind))
                st["empty_tiles"] = st.get("empty_tiles", 0) + 1
                log(f"{prefix} skipped  no beaches in the tile")
                continue
            if fetched_any:
                time.sleep(SLEEP_BETWEEN_TILES)
            fetched_any = True
            st["calls"] += 1
            started = time.time()
            try:
                payload = fetch_tile(tile, raw_dir, endpoints, kind, log=log)
            except FetchError as err:
                st["errors"] += 1
                st["failed_tiles"].append(tile.id)
                log(f"{prefix} FAILED   {err}")
                continue
            finally:
                st["seconds"] = round(st["seconds"] + time.time() - started, 1)
            st["ok"] += 1
            st["endpoint"] = payload["endpoint"]
            log(f"{prefix} fetched  {summarize(payload, kind)}  {payload['seconds']}s")
    for st in status.values():
        if st["failed_tiles"]:
            st["note"] = f"{len(st['failed_tiles'])} tiles failed; re-run to resume"
    return status


def load_tiles(raw_dir: Path, kind: str = "osm") -> list[dict]:
    """All cached tile payloads of one kind for a run, sorted by tile id."""
    return [json.loads(p.read_text()) for p in sorted((raw_dir / kind).glob("*.json"))]
