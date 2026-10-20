"""Webcams, surf spots, surf shops, lifeguard posts, and amenities near beaches.

Pulled by a second Overpass query per tile (raw/<run-date>/osm_nearby/), then
attached to every beach within ATTACH_RADIUS_M in build.
"""

from __future__ import annotations

import math

from .geometry import M_PER_DEG_LAT, M_PER_DEG_LNG, point_in_polygon
from .hashing import sha256

ATTACH_RADIUS_M = 300.0
# A few beaches can legitimately miss the canary (for example a relation with
# no way members), so only a real drop counts as a server failure.
CANARY_MIN_SHARE = 0.9
INDEX_CELL_DEG = 0.01
KINDS = ("webcam", "surf_spot", "surf_shop", "lifeguard", "parking", "toilets", "shower",
         "drinking_water")
WEBCAM_KEYS = ("contact:webcam", "webcam", "url:webcam")
AMENITY_KINDS = ("parking", "toilets", "shower", "drinking_water")


def build_query(bbox: tuple[float, float, float, float]) -> str:
    """Beaches in the tile, then each kind of item within ATTACH_RADIUS_M of them.

    Each filter carries its own `around` clause. (Filtering a prepared set with
    `nwr.k(around.b:300)` returned nothing on overpass-api.de in testing.)
    """
    s, w, n, e = bbox
    box = f"({s},{w},{n},{e})"
    filters = [
        '["man_made"="surveillance"]["surveillance:type"="webcam"]',
        '["contact:webcam"]', '["webcam"]', '["url:webcam"]',
        '["sport"="surfing"]', '["natural"="reef"]["name"]',
        '["shop"="surf"]', '["amenity"="surf_school"]',
        '["emergency"="lifeguard"]',
        f'["amenity"~"^({"|".join(AMENITY_KINDS)})$"]',
    ]
    radius = f"{ATTACH_RADIUS_M:g}"
    lines = ["[out:json][timeout:300];",
             f'(nwr["natural"="beach"]{box}; nwr["leisure"="beach_resort"]{box};)->.b;', "("]
    lines += [f"  nwr{f}(around.b:{radius});" for f in filters]
    lines += [");", "out meta center qt;",
              # canary: every beach is within 1 m of itself, so this count should
              # match the beach count. Far less means `around` failed on the
              # server (seen on overpass-api.de: ways dropped, nodes kept).
              ".b out count;", 'nwr["natural"="beach"](around.b:1);', "out count;"]
    return "\n".join(lines) + "\n"


def check_canary(response: dict) -> None:
    """Raise FetchError when the server's `around` silently returned nothing."""
    from .http import FetchError
    counts = [int(e["tags"]["total"]) for e in response.get("elements", [])
              if e.get("type") == "count"]
    if len(counts) != 2:
        raise FetchError("nearby response is missing its canary counts")
    beaches, canary = counts
    if canary < CANARY_MIN_SHARE * beaches:
        raise FetchError(f"around found {canary} of {beaches} beaches within 1 m of themselves "
                         "(server-side); retrying")


def kind_of(tags: dict) -> str | None:
    """One kind per item, first match wins in this order."""
    if (tags.get("man_made") == "surveillance" and tags.get("surveillance:type") == "webcam") \
            or any(k in tags for k in WEBCAM_KEYS):
        return "webcam"
    if tags.get("emergency") == "lifeguard":
        return "lifeguard"
    if tags.get("shop") == "surf" or tags.get("amenity") == "surf_school" or (
            tags.get("sport") == "surfing" and ("shop" in tags or "club" in tags)):
        return "surf_shop"
    if tags.get("sport") == "surfing" or (tags.get("natural") == "reef" and tags.get("name")):
        return "surf_spot"
    if tags.get("amenity") in AMENITY_KINDS:
        return tags["amenity"]
    return None


def webcam_url(tags: dict) -> str | None:
    for key in WEBCAM_KEYS:
        value = (tags.get(key) or "").strip()
        if value.startswith(("http://", "https://")):
            return value
    return None


def item_point(el: dict) -> tuple[float, float] | None:
    if "lat" in el:
        return el["lat"], el["lon"]
    c = el.get("center")
    return (c["lat"], c["lon"]) if c else None


def item_record(el: dict) -> dict | None:
    if el.get("type") not in ("node", "way", "relation"):
        return None
    tags = el.get("tags", {})
    kind, point = kind_of(tags), item_point(el)
    if not kind or not point:
        return None
    return {
        "kind": kind, "osm_type": el["type"], "osm_id": el["id"],
        "osm_url": f"https://www.openstreetmap.org/{el['type']}/{el['id']}",
        "osm_version": el.get("version"), "name": tags.get("name"),
        "latitude": round(point[0], 7), "longitude": round(point[1], 7),
        "distance_m": None,
        "website": tags.get("website") or tags.get("contact:website"),
        "webcam_url": webcam_url(tags),
        "tags": dict(sorted(tags.items())),
    }


def _local(lat: float, lng: float, lat0: float, lng0: float) -> tuple[float, float]:
    return ((lng - lng0) * M_PER_DEG_LNG * math.cos(math.radians(lat0)),
            (lat - lat0) * M_PER_DEG_LAT)


def _seg(px: float, py: float, a: tuple, b: tuple) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == 0 and dy == 0:
        return math.hypot(px - a[0], py - a[1])
    t = max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - a[0] - t * dx, py - a[1] - t * dy)


def distance_m(lat: float, lng: float, shape: dict) -> float:
    """Metres from a point to a beach shape: 0 inside a polygon, else the
    nearest ring or line segment, or the beach point."""
    if shape["kind"] == "polygon":
        outers, holes = shape["rings"], shape["holes"]
        if point_in_polygon(lat, lng, outers, holes):
            return 0.0
        lines = outers + holes
    elif shape["kind"] == "line":
        lines = [shape["line"]]
    else:
        x, y = _local(shape["point"][0], shape["point"][1], lat, lng)
        return math.hypot(x, y)
    best = math.inf
    for line in lines:
        pts = [_local(a, b, lat, lng) for a, b in line]
        for p, q in zip(pts, pts[1:]):
            best = min(best, _seg(0.0, 0.0, p, q))
    return best


def _cells(s: float, w: float, n: float, e: float):
    for i in range(math.floor(s / INDEX_CELL_DEG), math.floor(n / INDEX_CELL_DEG) + 1):
        for j in range(math.floor(w / INDEX_CELL_DEG), math.floor(e / INDEX_CELL_DEG) + 1):
            yield i, j


def attach(shapes: dict[str, dict], items: list[dict]) -> dict[str, list[dict]]:
    """Beach id -> items within ATTACH_RADIUS_M, sorted by kind, distance, id.

    `shapes[id]` is {"kind": polygon|line|point, "bbox": (s, w, n, e), ...}.
    """
    index: dict[tuple, list[str]] = {}
    for rid, shape in shapes.items():
        s, w, n, e = shape["bbox"]
        dlat = ATTACH_RADIUS_M / M_PER_DEG_LAT
        dlng = ATTACH_RADIUS_M / (M_PER_DEG_LNG * max(math.cos(math.radians(n if n > 0 else s)), 0.01))
        for cell in _cells(s - dlat, w - dlng, n + dlat, e + dlng):
            index.setdefault(cell, []).append(rid)
    out: dict[str, list[dict]] = {}
    for item in items:
        lat, lng = item["latitude"], item["longitude"]
        cell = (math.floor(lat / INDEX_CELL_DEG), math.floor(lng / INDEX_CELL_DEG))
        for rid in index.get(cell, []):
            d = distance_m(lat, lng, shapes[rid])
            if d <= ATTACH_RADIUS_M:
                out.setdefault(rid, []).append({**item, "distance_m": round(d, 1)})
    for rid in out:
        out[rid].sort(key=lambda it: (KINDS.index(it["kind"]), it["distance_m"],
                                      it["osm_type"], it["osm_id"]))
    return out


def nearby_hash(items: list[dict]) -> str:
    """SHA-256 over the attached items' type, id, version, and sorted tags."""
    return sha256(sorted([it["osm_type"], it["osm_id"], it["osm_version"],
                          sorted(it["tags"].items())] for it in items))


def counts(items: list[dict]) -> dict[str, int]:
    found = {k: 0 for k in KINDS}
    for it in items:
        found[it["kind"]] += 1
    return {k: v for k, v in found.items() if v}
