"""The geometry block: raw OSM tiles -> core beach fields, boundary, area, grid."""

from __future__ import annotations

import math
import re
import urllib.parse
from collections import Counter

from . import grid as gridlib
from .geometry import (Ring, clean_ring, interior_point, join_ways, polygon_area_m2,
                       simplify_ring)
from . import merge, shore, water as waterlib
from .hashing import element_hash, sha256
from .merge import union_area_m2
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


def shape(el: dict, lat0: float, lng0: float, light: bool = False,
          others: list[dict] | None = None, water: list[dict] | None = None,
          water_override: list | None = None) -> dict:
    """area_m2, simplified boundary with its water edge, grid with per-cell
    water distance and edge flags, and a note, for one element or for a
    merged beach (`el` the primary member, `others` the rest).

    `water` is the water features near the beach (shore.feature);
    `water_override` replaces the detected water edge (overrides file).
    `light` skips the water edge and the grid (used by pull to rank beaches).
    """
    members = [el] + list(others or [])
    polygons = []
    notes = []
    for m in members:
        outers, inners, note = raw_rings(m)
        notes.append(note)
        if outers:
            polygons.append((outers, inners))
    out = {"note": notes[0] if len(members) == 1 else "merged", "area_m2": None,
           "boundary": None, "grid": None, "light": light, "derive": DERIVE_KEY,
           "water": None}
    if not polygons:
        return out
    if len(polygons) == 1:
        out["area_m2"] = round(polygon_area_m2(*polygons[0], lat0, lng0))
    else:
        out["area_m2"] = round(union_area_m2(polygons, lat0, lng0))
    simplified = [([_round_ring(simplify_ring(r, SIMPLIFY_TOLERANCE_M, lat0, lng0)) for r in o],
                   [_round_ring(simplify_ring(r, SIMPLIFY_TOLERANCE_M, lat0, lng0)) for r in h])
                  for o, h in polygons]
    s_out = [r for o, _ in simplified for r in o]
    s_in = [r for _, h in simplified for r in h]
    boundary = {
        "rings": [[list(p) for p in r] for r in s_out],
        "point_count": sum(len(r) for r in s_out),
        "simplified_from": sum(len(r) for o, _ in polygons for r in o),
    }
    if s_in:
        boundary["holes"] = [[list(p) for p in r] for r in s_in]
    if len(simplified) > 1:
        # rings may overlap on a merged beach: which rings and holes belong to
        # which member, so a point test can use union semantics
        boundary["parts"] = []
        ri = hi = 0
        for o, h in simplified:
            boundary["parts"].append({"rings": list(range(ri, ri + len(o))),
                                      "holes": list(range(hi, hi + len(h)))})
            ri, hi = ri + len(o), hi + len(h)
    out["boundary"] = boundary
    if light:
        return out
    found = shore.water_edge(simplified, water or [], lat0, lng0, water_override)
    boundary["water_edge"] = found["edge"]
    out["water"] = {"kind": found["kind"], "edge_m": found["length_m"],
                    "orientation": found["orientation"]}
    heading = shore.heading_from(found["seaward"], None) if found["seaward"] else None
    grid = gridlib.build_grid(s_out, s_in, simplified if len(simplified) > 1 else None, heading)
    if grid:
        grid["water_dm"], grid["edge"] = shore.cell_facts(grid, simplified, found, lat0, lng0)
    out["grid"] = grid
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


DERIVE_KEY = (f"tol={SIMPLIFY_TOLERANCE_M}|cell={gridlib.CELL_SIZE_M}|dec={COORD_DECIMALS}"
              f"|edge={shore.EDGE_TOL_M}|water={waterlib.SEARCH_M}|y=up")


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
        "osm_ids": [f"{el['type']}/{el['id']}"],
        "merged_from": [],
        "tags": dict(sorted(tags.items())),
    }


MERGE_FILL = ("name", "wikipedia", "wikipedia_url", "wikidata", "wikidata_url", "surface",
              "access", "fee", "dog", "lifeguard", "wheelchair", "nudist", "operator", "website")


def _member(el: dict, tags: dict, lat: float, lon: float, bbox) -> dict:
    """What merge.groups needs to know about one element."""
    outers, inners, _ = raw_rings(el)
    m = {"type": el["type"], "id": el["id"], "name": tags.get("name"), "el": el,
         "kind": "polygon" if outers else "point", "area": None}
    if outers:
        m.update(outers=outers, inners=inners,
                 area=polygon_area_m2(outers, inners, lat, lon))
        lats = [p[0] for r in outers for p in r]
        lngs = [p[1] for r in outers for p in r]
        m["bbox"] = (min(lats), min(lngs), max(lats), max(lngs))
        m["point"] = (lat, lon)
    else:
        geom = [g for g in el.get("geometry") or [] if g]
        m["point"] = (geom[len(geom) // 2]["lat"], geom[len(geom) // 2]["lon"]) if geom \
            else (lat, lon)
        m["bbox"] = (m["point"][0], m["point"][1], m["point"][0], m["point"][1])
    return m


def group_bbox(group: list[dict]) -> tuple[float, float, float, float]:
    boxes = [m["bbox"] for m in group]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def merged_core(cores: list[dict], bboxes: list) -> dict:
    """The primary member's fields, gaps filled from the other members, names
    pooled into alt_names, and every member's OSM keys kept."""
    core = dict(cores[0])
    for c in cores[1:]:
        for key in MERGE_FILL:
            if core.get(key) is None and c.get(key) is not None:
                core[key] = c[key]
    names: list[str] = []
    for c in cores:
        for n in [c.get("name")] + (c.get("alt_names") or []):
            if n and n != core["name"] and n not in names:
                names.append(n)
    core["alt_names"] = names or None
    core["is_resort"] = any(c["is_resort"] for c in cores)
    core["protected_area_hint"] = any(c["protected_area_hint"] for c in cores)
    boxes = [b for b in bboxes if b]
    if boxes:
        box = [min(b[0] for b in boxes), min(b[1] for b in boxes),
               max(b[2] for b in boxes), max(b[3] for b in boxes)]
        core["bbox"] = box
        core["latitude"] = round((box[0] + box[2]) / 2, COORD_DECIMALS)
        core["longitude"] = round((box[1] + box[3]) / 2, COORD_DECIMALS)
    core["merged_from"] = [{k: c[k] for k in ("osm_type", "osm_id", "osm_url", "osm_version",
                                               "osm_timestamp", "osm_changeset", "name", "tags")}
                           for c in cores[1:]]
    return core


class WaterIndex:
    """Water features from the osm_water tiles, bucketed by 0.01 degree."""

    def __init__(self, payloads: list[dict]):
        self.features: list[dict] = []
        self.cells: dict[tuple[int, int], list[int]] = {}
        seen: set[tuple[str, int]] = set()
        for p in payloads:
            region = p["tile"].get("region")
            for el in p["response"]["elements"]:
                key = (el["type"], el["id"])
                if el["type"] not in ("way", "relation") or key in seen:
                    continue
                seen.add(key)
                kind = waterlib.kind_of(el.get("tags", {}))
                if kind == "sea" and region == "great_lakes":
                    kind = "lake"  # the Great Lakes are mapped with coastline
                f = shore.feature(el, kind) if kind else None
                if not f:
                    continue
                f["great_lakes"] = region == "great_lakes"
                f["key"] = f"{el['type']}/{el['id']}"
                f["hash"] = el.get("source_hash") or element_hash(el)
                f["bbox"] = shore._bbox(f["lines"])
                f["coastline"] = el.get("tags", {}).get("natural") == "coastline"
                self.features.append(f)
        self._open_to_sea()
        for i, f in enumerate(self.features):
            s_, w, n, e = f["bbox"]
            for a in range(math.floor(s_ * 100), math.floor(n * 100) + 1):
                for b in range(math.floor(w * 100), math.floor(e * 100) + 1):
                    self.cells.setdefault((a, b), []).append(i)

    def _open_to_sea(self) -> None:
        """A lake or river polygon whose outline comes within
        water.OPENS_TO_SEA_M of the coastline opens to the sea: its water is
        tidal, so it counts as sea (a lagoon, a harbour, a river mouth)."""
        step = 0.001
        coast: dict[tuple[int, int], list] = {}
        for f in self.features:
            if not f["coastline"]:
                continue
            for line in f["lines"]:
                for a, b in zip(line, line[1:]):
                    for i in range(math.floor(min(a[0], b[0]) / step), math.floor(max(a[0], b[0]) / step) + 1):
                        for j in range(math.floor(min(a[1], b[1]) / step),
                                       math.floor(max(a[1], b[1]) / step) + 1):
                            coast.setdefault((i, j), []).append((a, b))
        if not coast:
            return
        for f in self.features:
            if f["kind"] == "sea" or not f["polygons"]:
                continue
            for line in f["lines"]:
                if any(self._near_coast(coast, lat, lng, step) for lat, lng in line):
                    f["kind"] = "sea" if f["kind"] != "lake" or not f["great_lakes"] else "lake"
                    f["opens_to_sea"] = True
                    break

    @staticmethod
    def _near_coast(coast: dict, lat: float, lng: float, step: float) -> bool:
        i, j = math.floor(lat / step), math.floor(lng / step)
        kx = 111320.0 * math.cos(math.radians(lat))
        for a in (i - 1, i, i + 1):
            for b in (j - 1, j, j + 1):
                for (y1, x1), (y2, x2) in coast.get((a, b), ()):
                    ax, ay = (x1 - lng) * kx, (y1 - lat) * 110540.0
                    bx, by = (x2 - lng) * kx, (y2 - lat) * 110540.0
                    if shore._seg_dist(0.0, 0.0, ax, ay, bx, by) <= waterlib.OPENS_TO_SEA_M:
                        return True
        return False

    def near(self, bbox) -> list[dict]:
        """Features whose box comes within water.SEARCH_M of the beach box."""
        s_, w, n, e = bbox
        dlat = waterlib.SEARCH_M / 110540.0
        dlng = waterlib.SEARCH_M / (111320.0 * max(math.cos(math.radians(s_)), 0.01))
        s_, w, n, e = s_ - dlat, w - dlng, n + dlat, e + dlng
        found = set()
        for a in range(math.floor(s_ * 100), math.floor(n * 100) + 1):
            for b in range(math.floor(w * 100), math.floor(e * 100) + 1):
                for i in self.cells.get((a, b), ()):
                    fs, fw, fn, fe = self.features[i]["bbox"]
                    if fs <= n and s_ <= fn and fw <= e and w <= fe:
                        found.add(i)
        return [self.features[i] for i in sorted(found, key=lambda i: self.features[i]["key"])]


def geometry_blocks(tiles: list[dict], cached: dict[str, dict], light: bool = False,
                    water_tiles: list[dict] | None = None, overrides: dict | None = None,
                    ) -> tuple[dict[str, dict], Counter]:
    """Beach id -> geometry block {"hash", "data"} from this run's raw tiles.

    Overlapping elements are merged first (merge.py). `cached[id]` is the
    previous geometry block (read back from the previous pack); when its hash
    matches (the element's source_hash, or for a merged beach the hash over
    its members' source hashes) and the shape settings are unchanged, its
    area, boundary, and grid are reused.
    """
    codes, seen_in, country_found = memberships(tiles)
    report: Counter = Counter()
    suspect = sum(1 for t in tiles if t.get("suspect"))
    if suspect:
        report["suspect_tiles"] = suspect
    found: dict[tuple, dict] = {}
    for payload in tiles:
        for el in payload["response"]["elements"]:
            key = (el["type"], el["id"])
            if el["type"] in (MARKER_TYPE, "count") or key in found:
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
            m = _member(el, tags, lat, lon, bbox)
            m.update(core=core_fields(el, tags, lat, lon, bbox, where), lat=lat, lon=lon,
                     tiles=tiles_of, hash=el.get("source_hash") or element_hash(el))
            found[key] = m
    out: dict[str, dict] = {}
    waters = WaterIndex(water_tiles or [])
    overrides = overrides or {}
    for group in merge.groups(list(found.values())):
        head = group[0]
        if len(group) == 1:
            core, source_hash = head["core"], head["hash"]
        else:
            report["merged_beaches"] += 1
            report["merged_elements"] += len(group)
            core = merged_core([m["core"] for m in group], [m["core"]["bbox"] for m in group])
            source_hash = sha256(sorted(m["hash"] for m in group))
        core["osm_ids"] = [f"{m['type']}/{m['id']}" for m in group]
        near = waters.near(group_bbox(group)) if not light else []
        edge_override = (overrides.get(core["id"]) or {}).get("water_edge")
        if near or edge_override:
            # the shape depends on the water too: hash the beach with its water
            source_hash = sha256([source_hash, sorted([f["key"], f["hash"], f["kind"]] for f in near),
                                  edge_override])
        old = cached.get(core["id"])
        old_shape = old["data"]["shape"] if old else {}
        if old and old.get("hash") == source_hash and not old_shape.get("light") \
                and old_shape.get("derive") == DERIVE_KEY:
            shp = old_shape
            report["shapes_reused"] += 1
        else:
            shp = shape(head["el"], head["lat"], head["lon"], light, [m["el"] for m in group[1:]],
                        near, edge_override)
            report["shapes_computed"] += 1
            report[f"geometry_{shp['note']}"] += 1
        out[core["id"]] = {"hash": source_hash, "data": {
            "core": core, "shape": shp,
            "tiles": sorted({t["id"] for m in group for t in m["tiles"]})}}
    report["beaches"] = len(out)
    return out, report


def sort_key(rec: dict) -> tuple:
    return (rec["state"] is None, rec["state"] or "", rec["name"] is None,
            (rec["name"] or "").casefold(), rec["id"])
