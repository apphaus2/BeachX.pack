"""The geometry block: raw OSM tiles -> core beach fields, boundary, area, grid."""

from __future__ import annotations

import re
import urllib.parse
from collections import Counter

from . import grid as gridlib
from .geometry import (Ring, clean_ring, interior_point, join_ways, polygon_area_m2,
                       simplify_ring)
from .hashing import element_hash
from .overpass import MARKER_TYPE
from .tiles import COUNTRY_OF_STATE, state_code, states_for_point

SIMPLIFY_TOLERANCE_M = 3.0
COORD_DECIMALS = 7
PREFIX = {"node": "n", "way": "w", "relation": "r"}

# protected_area_hint: any of these phrases in name or operator (case-insensitive) ...
PROTECTED_WORDS = (
    "state park", "state beach", "state recreation area", "state seashore",
    "national park", "national seashore", "national lakeshore",
    "national recreation area", "national wildlife refuge", "wildlife refuge",
    "national monument", "county park", "regional park", "recreation area",
    "beach park", "nature preserve", "preserve", "reserve", "wildlife area",
    "park service", "parks and recreation", "department of parks",
    "department of natural resources", "fish and wildlife",
)
# ... or any of these tags on the beach element itself.
PROTECTED_TAGS = {("boundary", "protected_area"), ("boundary", "national_park"),
                  ("leisure", "nature_reserve"), ("leisure", "park")}


QID = re.compile(r"^Q[1-9][0-9]*$")
WIKI_LANG = re.compile(r"^[a-z][a-z0-9-]{1,11}$")


def beach_id(osm_type: str, osm_id: int) -> str:
    return f"beach_osm_{PREFIX[osm_type]}{osm_id}"


def bare_qid(value: str | None) -> str | None:
    """The `wikidata` tag when it is exactly one QID, else None (raw stays in tags)."""
    text = (value or "").strip()
    return text if QID.match(text) else None


def wikipedia_url(value: str | None) -> str | None:
    """`<lang>:<title>` -> https://<lang>.wikipedia.org/wiki/<Title_with_underscores>.
    The title is percent-encoded. None when the tag has no language prefix."""
    lang, sep, title = (value or "").partition(":")
    lang, title = lang.strip().lower(), title.strip()
    if not sep or not title or not WIKI_LANG.match(lang):
        return None
    path = urllib.parse.quote(title.replace(" ", "_"), safe="()_,'!:*-.~/")
    return f"https://{lang}.wikipedia.org/wiki/{path}"


def osm_links(osm_type: str, osm_id: int) -> tuple[str, str]:
    return (f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
            f"https://www.openstreetmap.org/edit?{osm_type}={osm_id}")


def protected_hint(tags: dict) -> bool:
    text = " ".join(tags.get(k, "") for k in ("name", "operator")).lower()
    if any(word in text for word in PROTECTED_WORDS):
        return True
    return any(tags.get(k) == v for k, v in PROTECTED_TAGS)


def alt_names(tags: dict) -> list[str] | None:
    names: list[str] = []
    for key in ("alt_name", "official_name", "name:en"):
        for part in tags.get(key, "").split(";"):
            part = part.strip()
            if part and part != tags.get("name") and part not in names:
                names.append(part)
    return names or None


def raw_rings(el: dict) -> tuple[list[Ring], list[Ring], str]:
    """Outer and inner rings from Overpass `out geom`, plus a geometry note."""
    def pts(geom: list) -> list[tuple[float, float]]:
        return [(g["lat"], g["lon"]) for g in geom or [] if g]

    if el["type"] == "way":
        points = pts(el.get("geometry"))
        if len(points) < 4 or points[0] != points[-1]:
            return [], [], "open_way"
        ring = clean_ring(points)
        return ([ring], [], "ok") if ring else ([], [], "degenerate")
    if el["type"] == "relation":
        outer, inner = [], []
        for m in el.get("members", []):
            if m.get("type") != "way":
                continue
            (inner if m.get("role") == "inner" else outer).append(pts(m.get("geometry")))
        outers, open_outer = join_ways(outer)
        inners, _ = join_ways(inner)
        if not outers:
            return [], [], "unassembled_relation"
        return outers, inners, "ok" if not open_outer else "partial_relation"
    return [], [], "node"


def _round_ring(ring: Ring) -> Ring:
    return [(round(a, COORD_DECIMALS), round(b, COORD_DECIMALS)) for a, b in ring]


def shape(el: dict, lat0: float, lng0: float, light: bool = False) -> dict:
    """area_m2, simplified boundary, grid, and a note for one element.

    `light` skips the grid (used by pull to rank beaches).
    """
    outers, inners, note = raw_rings(el)
    out = {"note": note, "area_m2": None, "boundary": None, "grid": None, "light": light,
           "derive": DERIVE_KEY}
    if not outers:
        return out
    out["area_m2"] = round(polygon_area_m2(outers, inners, lat0, lng0))
    s_out = [_round_ring(simplify_ring(r, SIMPLIFY_TOLERANCE_M, lat0, lng0)) for r in outers]
    s_in = [_round_ring(simplify_ring(r, SIMPLIFY_TOLERANCE_M, lat0, lng0)) for r in inners]
    boundary = {
        "rings": [[list(p) for p in r] for r in s_out],
        "point_count": sum(len(r) for r in s_out),
        "simplified_from": sum(len(r) for r in outers),
    }
    if s_in:
        boundary["holes"] = [[list(p) for p in r] for r in s_in]
    out["boundary"] = boundary
    if not light:
        out["grid"] = gridlib.build_grid(s_out, s_in)
    return out


def lookup_point(rec: dict, boundary: dict | None) -> tuple[float, float]:
    """Where per-point sources are asked: inside the simplified polygon when
    there is one, else the beach point."""
    if boundary:
        outers = [[tuple(p) for p in r] for r in boundary["rings"]]
        holes = [[tuple(p) for p in r] for r in boundary.get("holes", [])]
        lat, lng = interior_point(outers, holes)
        return round(lat, COORD_DECIMALS), round(lng, COORD_DECIMALS)
    return rec["latitude"], rec["longitude"]


def memberships(tiles: list[dict]) -> tuple[dict, dict, dict]:
    """Per element key: area codes it is in, the tiles it came from, and per tile
    whether its country area resolved."""
    codes: dict[tuple, set] = {}
    seen_in: dict[tuple, list] = {}
    country_found: dict[str, bool] = {}
    for payload in tiles:
        tid = payload["tile"]["id"]
        country_found[tid] = False
        for el in payload["response"]["elements"]:
            if el["type"] == "count":
                continue
            if el["type"] != MARKER_TYPE:
                seen_in.setdefault((el["type"], el["id"]), []).append(payload["tile"])
                continue
            if payload.get("suspect"):  # area answer looked broken: drop nothing here
                continue
            tags = el["tags"]
            if tags["code"] == payload["tile"]["country"] and tags.get("area_ids"):
                country_found[tid] = True
            for member in filter(None, tags.get("members", "").split(";")):
                kind, _, num = member.partition("/")
                codes.setdefault((kind, int(num)), set()).add(tags["code"])
    return codes, seen_in, country_found


def place(tags: dict, lat: float, lon: float, codes: set, tiles_of: list[dict],
          country_known: bool) -> tuple[str | None, str | None, str | None]:
    """(state, state_confidence, country), or country None for a foreign beach."""
    countries = [c for c in ("US", "PR", "VI") if c in codes]
    if countries:
        country = countries[0]
    elif country_known:
        return None, None, None
    else:
        country = tiles_of[0]["country"]
    if country in ("PR", "VI"):
        return country, "area", country
    tag_state = state_code(tags.get("addr:state")) or state_code(tags.get("is_in:state")) \
        or state_code(tags.get("is_in:state_code"))
    states = sorted(c[3:] for c in codes if c.startswith("US-"))
    primary = tiles_of[0]["state"]
    if len(states) == 1:
        return states[0], "area", country
    if states:
        if tag_state in states:
            return tag_state, "tag", country
        return (primary if primary in states else states[0]), "area", country
    if tag_state:
        return tag_state, "tag", COUNTRY_OF_STATE.get(tag_state, country)
    boxed = states_for_point(lat, lon)
    if not boxed:
        return None, None, country
    return (primary if primary in boxed else boxed[0]), "bbox", country


DERIVE_KEY = f"tol={SIMPLIFY_TOLERANCE_M}|cell={gridlib.CELL_SIZE_M}|dec={COORD_DECIMALS}"


def core_fields(el: dict, tags: dict, lat: float, lon: float, bbox, where: tuple) -> dict:
    """Record fields that come straight from the OSM element."""
    state, confidence, country = where
    osm_url, osm_edit_url = osm_links(el["type"], el["id"])
    qid = bare_qid(tags.get("wikidata"))
    return {
        "id": beach_id(el["type"], el["id"]), "osm_type": el["type"], "osm_id": el["id"],
        "osm_url": osm_url, "osm_edit_url": osm_edit_url,
        "osm_version": el.get("version"), "osm_timestamp": el.get("timestamp"),
        "osm_changeset": el.get("changeset"),
        "name": tags.get("name"), "alt_names": alt_names(tags),
        "latitude": round(lat, COORD_DECIMALS), "longitude": round(lon, COORD_DECIMALS),
        "bbox": bbox,
        "state": state, "state_confidence": confidence, "country": country,
        "surface": tags.get("surface"), "access": tags.get("access"),
        "fee": tags.get("fee"), "dog": tags.get("dog"),
        "lifeguard": tags.get("lifeguard") or tags.get("supervised"),
        "wheelchair": tags.get("wheelchair"), "nudist": tags.get("nudism"),
        "operator": tags.get("operator"),
        "website": tags.get("website") or tags.get("contact:website"),
        "wikipedia": tags.get("wikipedia"),
        "wikipedia_url": wikipedia_url(tags.get("wikipedia")),
        "wikidata": qid,
        "wikidata_url": f"https://www.wikidata.org/wiki/{qid}" if qid else None,
        "is_resort": tags.get("leisure") == "beach_resort",
        "protected_area_hint": protected_hint(tags),
        "osm_tags_count": len(tags),
        "tags": dict(sorted(tags.items())),
    }


def geometry_blocks(tiles: list[dict], cached: dict[str, dict], light: bool = False,
                    ) -> tuple[dict[str, dict], Counter]:
    """Beach id -> geometry block {"hash", "data"} from this run's raw tiles.

    `cached[id]` is the previous geometry block (read back from the previous
    pack); when its hash matches the element's source_hash and the shape
    settings are unchanged, its area, boundary, and grid are reused.
    """
    codes, seen_in, country_found = memberships(tiles)
    report: Counter = Counter()
    suspect = sum(1 for t in tiles if t.get("suspect"))
    if suspect:
        report["suspect_tiles"] = suspect
    out: dict[str, dict] = {}
    for payload in tiles:
        for el in payload["response"]["elements"]:
            key = (el["type"], el["id"])
            if el["type"] in (MARKER_TYPE, "count") or beach_id(*key) in out:
                continue
            report["elements"] += 1
            tiles_of = seen_in[key]
            tags = el.get("tags", {})
            if el["type"] == "node":
                lat, lon, bbox = el["lat"], el["lon"], None
            else:
                b = el.get("bounds")
                if not b:
                    report["skipped_no_bounds"] += 1
                    continue
                bbox = [b["minlat"], b["minlon"], b["maxlat"], b["maxlon"]]
                lat, lon = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
            known = any(country_found.get(t["id"]) for t in tiles_of)
            where = place(tags, lat, lon, codes.get(key, set()), tiles_of, known)
            if where[2] is None:
                report["excluded_foreign"] += 1
                continue
            if not known:
                report["country_area_unresolved"] += 1
            source_hash = el.get("source_hash") or element_hash(el)
            core = core_fields(el, tags, lat, lon, bbox, where)
            old = cached.get(core["id"])
            old_shape = old["data"]["shape"] if old else {}
            if old and old.get("hash") == source_hash and not old_shape.get("light") \
                    and old_shape.get("derive") == DERIVE_KEY:
                shp = old_shape
                report["shapes_reused"] += 1
            else:
                shp = shape(el, lat, lon, light)
                report["shapes_computed"] += 1
                report[f"geometry_{shp['note']}"] += 1
            out[core["id"]] = {"hash": source_hash, "data": {
                "core": core, "shape": shp,
                "tiles": sorted({t["id"] for t in tiles_of})}}
    return out, report


def sort_key(rec: dict) -> tuple:
    return (rec["state"] is None, rec["state"] or "", rec["name"] is None,
            (rec["name"] or "").casefold(), rec["id"])
