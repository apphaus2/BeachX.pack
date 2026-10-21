"""Overlapping OSM features become one beach.

Same sand mapped twice (two polygons that mostly overlap, a beach node
dropped inside a beach polygon, the same name pinned twice a few metres
apart) is one beach carrying every OSM id. Beaches that only share an edge
stay separate.

Rules:
- two polygons are the same sand when at least MERGE_SHARE of the smaller
  one lies inside the larger (estimated on a lattice of about LATTICE
  points);
- a point (a node, or an open way's middle vertex) is the same sand as a
  polygon it lies inside;
- two named points with the same name within POINT_RADIUS_M are the same.
Same sand merges only when the names agree: two members with different
names never merge, and an unnamed member that touches more than one
distinct name (one long unnamed outline with several named beaches pinned
on it) merges with none of them. Merges are transitive; a group that still
ends up with two names is split back into its members. The primary member (polygons first, then the
largest, then named, then way before relation before node, then the lowest
id) gives the beach its id, tags, and fields; the others are listed in
`merged_from`.
"""

from __future__ import annotations

import math

from .geometry import Ring, point_in_ring, polygon_area_m2, to_local

MERGE_SHARE = 0.5
LATTICE = 400
POINT_RADIUS_M = 100.0
TYPE_RANK = {"way": 0, "relation": 1, "node": 2}


def _inside(lat: float, lng: float, outers: list[Ring], inners: list[Ring]) -> bool:
    return (any(point_in_ring(lat, lng, r) for r in outers)
            and not any(point_in_ring(lat, lng, r) for r in inners))


def lattice(outers: list[Ring], inners: list[Ring], n: int = LATTICE) -> list[tuple[float, float]]:
    """About n evenly spaced points inside the polygon (at least its first vertex)."""
    lats = [p[0] for r in outers for p in r]
    lngs = [p[1] for r in outers for p in r]
    s, w, nn, e = min(lats), min(lngs), max(lats), max(lngs)
    side = max(int(math.sqrt(n)), 2)
    pts = []
    for i in range(side):
        lat = s + (nn - s) * (i + 0.5) / side
        for j in range(side):
            lng = w + (e - w) * (j + 0.5) / side
            if _inside(lat, lng, outers, inners):
                pts.append((lat, lng))
    return pts or [outers[0][0]]


def overlap_share(a: dict, b: dict) -> float:
    """Share of polygon a's lattice points that lie inside polygon b."""
    pts = a["_lattice"]
    return sum(_inside(lat, lng, b["outers"], b["inners"]) for lat, lng in pts) / len(pts)


def _boxes_touch(a: tuple, b: tuple) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _same_name(a: dict, b: dict) -> bool:
    return bool(a["name"]) and (a["name"] or "").casefold() == (b["name"] or "").casefold()


def primary_key(m: dict) -> tuple:
    return (m["kind"] != "polygon", -(m["area"] or 0), not m["name"],
            TYPE_RANK[m["type"]], m["id"])


def groups(members: list[dict]) -> list[list[dict]]:
    """Union-find over the merge rules. Each member is a dict with type, id,
    kind ("polygon" or "point"), name, bbox (s, w, n, e), point (lat, lng), and
    for polygons outers, inners, and area. Returns groups, primary first,
    sorted by the primary's (type, id)."""
    parent = list(range(len(members)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def join(i: int, j: int) -> None:
        a, b = find(i), find(j)
        if a != b:
            parent[max(a, b)] = min(a, b)

    polys = [i for i, m in enumerate(members) if m["kind"] == "polygon"]
    points = [i for i, m in enumerate(members) if m["kind"] == "point"]
    for i in polys:
        members[i]["_lattice"] = lattice(members[i]["outers"], members[i]["inners"])
    # bucket polygons by 0.01 degree cells of their box
    cells: dict[tuple[int, int], list[int]] = {}
    for i in polys:
        s, w, n, e = members[i]["bbox"]
        for a in range(math.floor(s * 100), math.floor(n * 100) + 1):
            for b in range(math.floor(w * 100), math.floor(e * 100) + 1):
                cells.setdefault((a, b), []).append(i)
    edges: set[tuple[int, int]] = set()
    seen: set[tuple[int, int]] = set()
    for idx in cells.values():
        for x in range(len(idx)):
            for y in range(x + 1, len(idx)):
                i, j = sorted((idx[x], idx[y]))
                if (i, j) in seen:
                    continue
                seen.add((i, j))
                a, b = members[i], members[j]
                if not _boxes_touch(a["bbox"], b["bbox"]):
                    continue
                small, big = (a, b) if (a["area"] or 0) <= (b["area"] or 0) else (b, a)
                if overlap_share(small, big) >= MERGE_SHARE:
                    edges.add((i, j))
    for i in points:
        lat, lng = members[i]["point"]
        for j in cells.get((math.floor(lat * 100), math.floor(lng * 100)), []):
            if _inside(lat, lng, members[j]["outers"], members[j]["inners"]):
                edges.add(tuple(sorted((i, j))))
    named = sorted((i for i in points if members[i]["name"]),
                   key=lambda i: (members[i]["name"].casefold(), i))
    for x, i in enumerate(named):
        for j in named[x + 1:]:
            if not _same_name(members[i], members[j]):
                break
            a, b = members[i]["point"], members[j]["point"]
            east, north = to_local(b[0], b[1], a[0], a[1])
            if math.hypot(east, north) <= POINT_RADIUS_M:
                edges.add((i, j))
    key = lambda i: (members[i]["name"] or "").casefold()  # noqa: E731
    edges = {(i, j) for i, j in edges if not key(i) or not key(j) or key(i) == key(j)}
    touches: dict[int, set[str]] = {}
    for i, j in edges:
        for u, v in ((i, j), (j, i)):
            if not key(u) and key(v):
                touches.setdefault(u, set()).add(key(v))
    crowded = {u for u, names in touches.items() if len(names) > 1}
    edges = {(i, j) for i, j in edges
             if not ((i in crowded and key(j)) or (j in crowded and key(i)))}
    for i, j in sorted(edges):
        join(i, j)
    groups_of: dict[int, list[int]] = {}
    for i in range(len(members)):
        groups_of.setdefault(find(i), []).append(i)
    for root, idx in groups_of.items():
        if len({key(i) for i in idx if key(i)}) > 1:  # still two names: undo
            for i in idx:
                parent[i] = i
    grouped: dict[int, list[dict]] = {}
    for i, m in enumerate(members):
        grouped.setdefault(find(i), []).append(m)
    out = [sorted(g, key=primary_key) for g in grouped.values()]
    for g in out:
        for m in g:
            m.pop("_lattice", None)
    return sorted(out, key=lambda g: (g[0]["type"], g[0]["id"]))


def union_area_m2(polygons: list[tuple[list[Ring], list[Ring]]], lat0: float, lng0: float) -> float:
    """Area of overlapping polygons: each one's area times the share of its
    lattice that no earlier polygon covers."""
    total = 0.0
    for k, (outers, inners) in enumerate(polygons):
        area = polygon_area_m2(outers, inners, lat0, lng0)
        if k:
            pts = lattice(outers, inners)
            free = sum(not any(_inside(a, b, o, h) for o, h in polygons[:k]) for a, b in pts)
            area *= free / len(pts)
        total += area
    return total
