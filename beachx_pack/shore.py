"""Water edge, orientation, and per-cell water distance and edge flags.

Everything here works in local metres around the beach (geometry.to_local)
and then in the grid frame (grid.to_grid).

- Water edge: the boundary segments that meet the water. A segment meets the
  water when at least half of its sample points (every SAMPLE_M) lie within
  EDGE_TOL_M of a coastline or water-polygon outline, or inside a water
  polygon. Consecutive meeting segments form one polyline.
- Orientation: the outward normals of the water-edge segments, weighted by
  length, point to the water. +y (the grid heading) is the opposite
  direction: up the beach. Without a water edge, the nearest water within
  water.SEARCH_M stands in; without any water, +y is perpendicular to the
  outline's long axis and its sign is arbitrary (orientation "outline").
- Water distance: from each cell centre to the nearest water-edge segment
  (or to the nearby water when there is no edge), None when there is no
  water at all. 0 means the cell lies against the water edge: its centre is
  within one cell size of it. (Rows run along the shore, so the edge often
  runs between the first row's centres and the outline without crossing
  any cell's square; "crosses the square" would leave whole beaches with no
  cell at the water.)
- Edge cells: cells whose square a boundary segment crosses.
"""

from __future__ import annotations

import math

from .geometry import Ring, from_local, to_local

EDGE_TOL_M = 10.0
SAMPLE_M = 2.5
BUCKET_M = 50.0
AMBIGUOUS = 0.1  # |sum of normals| below this share of the edge length: no clear side


# ---------------------------------------------------------------- water features

def feature(el: dict, kind: str) -> dict | None:
    """A water element as lines (lat, lng) plus, for polygons, (outers, inners)."""
    def pts(geom):
        return [(g["lat"], g["lon"]) for g in geom or [] if g]

    if el["type"] == "way":
        line = pts(el.get("geometry"))
        if len(line) < 2:
            return None
        closed = line[0] == line[-1] and len(line) >= 4
        poly = [([line], [])] if closed and kind != "sea" else []
        return {"kind": kind, "lines": [line], "polygons": poly}
    if el["type"] == "relation":
        from .geometry import join_ways
        outer, inner = [], []
        for m in el.get("members", []):
            if m.get("type") == "way" and m.get("geometry"):
                (inner if m.get("role") == "inner" else outer).append(pts(m["geometry"]))
        lines = [l for l in outer + inner if len(l) >= 2]
        if not lines:
            return None
        outers, _ = join_ways(outer)
        inners, _ = join_ways(inner)
        return {"kind": kind, "lines": lines, "polygons": [(outers, inners)] if outers else []}
    return None


def _bbox(lines: list[list[tuple[float, float]]]) -> tuple[float, float, float, float]:
    lats = [p[0] for l in lines for p in l]
    lngs = [p[1] for l in lines for p in l]
    return (min(lats), min(lngs), max(lats), max(lngs))


# ---------------------------------------------------------------- local geometry

def _seg_dist(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def _in_ring(x: float, y: float, ring: list[tuple[float, float]]) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _in_parts(x: float, y: float, parts: list) -> bool:
    return any(any(_in_ring(x, y, r) for r in o) and not any(_in_ring(x, y, h) for h in i)
               for o, i in parts)


class Segments:
    """Line segments in local metres, bucketed for nearest-distance queries."""

    def __init__(self, segs: list[tuple[float, float, float, float, str]]):
        self.segs = segs
        self.buckets: dict[tuple[int, int], list[int]] = {}
        for k, (ax, ay, bx, by, _) in enumerate(segs):
            for i in range(math.floor(min(ax, bx) / BUCKET_M), math.floor(max(ax, bx) / BUCKET_M) + 1):
                for j in range(math.floor(min(ay, by) / BUCKET_M),
                               math.floor(max(ay, by) / BUCKET_M) + 1):
                    self.buckets.setdefault((i, j), []).append(k)

    def nearest(self, x: float, y: float, limit: float) -> tuple[float, int]:
        """(distance, segment index) of the nearest segment within limit, else (inf, -1)."""
        best, which = math.inf, -1
        r = math.ceil(limit / BUCKET_M)
        ci, cj = math.floor(x / BUCKET_M), math.floor(y / BUCKET_M)
        seen = set()
        for i in range(ci - r, ci + r + 1):
            for j in range(cj - r, cj + r + 1):
                for k in self.buckets.get((i, j), ()):
                    if k in seen:
                        continue
                    seen.add(k)
                    ax, ay, bx, by, _ = self.segs[k]
                    d = _seg_dist(x, y, ax, ay, bx, by)
                    if d < best:
                        best, which = d, k
        return (best, which) if best <= limit else (math.inf, -1)


# ---------------------------------------------------------------- water edge

def water_edge(parts_ll: list, features: list[dict], lat0: float, lng0: float,
               override: list | None = None) -> dict:
    """The boundary stretch that meets the water, and which side is the water.

    parts_ll: [(outers, holes)] of (lat, lng) rings. Returns {"edge": polylines
    of [lat, lng], "kind", "length_m", "seaward": (east, north) or None,
    "segments": [(ax, ay, bx, by, kind)] local metres of the edge,
    "nearby": local segments of water within reach (when there is no edge)}.
    """
    parts = [([[to_local(a, b, lat0, lng0) for a, b in r] for r in o],
              [[to_local(a, b, lat0, lng0) for a, b in r] for r in i]) for o, i in parts_ll]
    wsegs, wpolys = [], []
    for f in features:
        for line in f["lines"]:
            loc = [to_local(a, b, lat0, lng0) for a, b in line]
            wsegs += [(p[0], p[1], q[0], q[1], f["kind"]) for p, q in zip(loc, loc[1:])]
        for o, i in f["polygons"]:
            wpolys.append(([[to_local(a, b, lat0, lng0) for a, b in r] for r in o],
                           [[to_local(a, b, lat0, lng0) for a, b in r] for r in i], f["kind"]))
    water = Segments(wsegs)

    def meets(x: float, y: float) -> str | None:
        d, k = water.nearest(x, y, EDGE_TOL_M)
        if k >= 0:
            return wsegs[k][4]
        for o, i, kind in wpolys:
            if any(_in_ring(x, y, r) for r in o) and not any(_in_ring(x, y, h) for h in i):
                return kind
        return None

    edge_ll: list[list[list[float]]] = []
    edge_segs: list[tuple[float, float, float, float, str]] = []
    seaward = [0.0, 0.0]
    length = 0.0
    by_kind: dict[str, float] = {}
    if override is not None:
        for line in override:
            loc = [to_local(a, b, lat0, lng0) for a, b in line]
            edge_ll.append([list(p) for p in line])
            for p, q in zip(loc, loc[1:]):
                edge_segs.append((p[0], p[1], q[0], q[1], "override"))
    else:
        for (outers, holes), (o_ll, h_ll) in zip(parts, parts_ll):
            for ring, ring_ll in zip(outers + holes, o_ll + h_ll):
                n = len(ring) - 1
                flags: list[str | None] = []
                for k in range(n):
                    (ax, ay), (bx, by) = ring[k], ring[k + 1]
                    steps = max(2, math.ceil(math.hypot(bx - ax, by - ay) / SAMPLE_M))
                    hits = [meets(ax + (bx - ax) * t / steps, ay + (by - ay) * t / steps)
                            for t in range(steps + 1)]
                    good = [h for h in hits if h]
                    flags.append(max(set(good), key=good.count) if 2 * len(good) >= len(hits)
                                 else None)
                if not any(flags):
                    continue
                # runs of consecutive meeting segments, wrapping around the ring
                start = next((k for k in range(n) if not flags[k]), None)
                order = list(range(n)) if start is None else \
                    [(start + 1 + k) % n for k in range(n)]
                run: list[int] = []
                runs: list[list[int]] = []
                for k in order:
                    if flags[k]:
                        run.append(k)
                    elif run:
                        runs.append(run)
                        run = []
                if run:
                    runs.append(run)
                for run in runs:
                    edge_ll.append([list(ring_ll[run[0]])] + [list(ring_ll[k + 1]) for k in run])
                    for k in run:
                        (ax, ay), (bx, by) = ring[k], ring[k + 1]
                        edge_segs.append((ax, ay, bx, by, flags[k]))
    for ax, ay, bx, by, kind in edge_segs:
        seg_len = math.hypot(bx - ax, by - ay)
        if seg_len == 0:
            continue
        length += seg_len
        by_kind[kind] = by_kind.get(kind, 0.0) + seg_len
        nx, ny = (by - ay) / seg_len, -(bx - ax) / seg_len
        mx, my = (ax + bx) / 2, (ay + by) / 2
        if _in_parts(mx + 0.5 * nx, my + 0.5 * ny, parts):  # points into the beach: flip
            nx, ny = -nx, -ny
        seaward[0] += nx * seg_len
        seaward[1] += ny * seg_len
    out = {"edge": edge_ll, "segments": edge_segs, "length_m": round(length, 1),
           "kind": None, "seaward": None, "nearby": [], "orientation": "outline"}
    if edge_segs:
        kinds = {k: v for k, v in by_kind.items() if k != "override"}
        if kinds:
            out["kind"] = max(sorted(kinds), key=kinds.get)
        if math.hypot(*seaward) >= AMBIGUOUS * length:
            out["seaward"] = (seaward[0], seaward[1])
            out["orientation"] = "water_edge"
        else:
            out["orientation"] = "ambiguous"
    if not edge_segs or out["seaward"] is None:
        # no edge (or no clear side): the nearest water within reach
        from .water import SEARCH_M
        pts = [p for o, i in parts for r in o for p in r[:-1]]
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        best = (math.inf, -1, None)
        for x, y in pts + [(cx, cy)]:
            d, k = water.nearest(x, y, SEARCH_M)
            if d < best[0]:
                best = (d, k, (x, y))
        if best[1] >= 0:
            ax, ay, bx, by, kind = wsegs[best[1]]
            x, y = best[2]
            dx, dy = bx - ax, by - ay
            t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy)
                                                       / (dx * dx + dy * dy)))
            wx, wy = ax + t * dx, ay + t * dy
            vx, vy = wx - cx, wy - cy
            if not edge_segs:
                out["kind"] = kind
                reach = best[0] + SEARCH_M
                out["nearby"] = [s for s in wsegs
                                 if min(_seg_dist(x, y, *s[:4]) for x, y in pts) <= reach]
            if math.hypot(vx, vy) > 0:
                out["seaward"] = (vx, vy)
                out["orientation"] = "nearby_water" if not edge_segs else "ambiguous_nearby"
    out["edge"] = [[[round(a, 7), round(b, 7)] for a, b in line] for line in out["edge"]]
    return out


def heading_from(seaward: tuple[float, float] | None, fallback: float) -> float:
    """Grid heading (degrees clockwise from north, [0, 360)) pointing away
    from the water. `fallback` is used when there is no water side."""
    if seaward is None:
        return fallback
    east, north = -seaward[0], -seaward[1]
    h = round(math.degrees(math.atan2(east, north)) % 360.0, 1)
    return 0.0 if h >= 360.0 else h


# ---------------------------------------------------------------- per cell

def _traverse(ax: float, ay: float, bx: float, by: float, size: float):
    """Cells (cx, cy) a segment passes through (Amanatides-Woo), in grid metres."""
    cx, cy = math.floor(ax / size), math.floor(ay / size)
    ex, ey = math.floor(bx / size), math.floor(by / size)
    yield cx, cy
    dx, dy = bx - ax, by - ay
    sx = 1 if dx > 0 else -1
    sy = 1 if dy > 0 else -1
    tdx = abs(size / dx) if dx else math.inf
    tdy = abs(size / dy) if dy else math.inf
    tx = ((cx + (sx > 0)) * size - ax) / dx if dx else math.inf
    ty = ((cy + (sy > 0)) * size - ay) / dy if dy else math.inf
    for _ in range(abs(ex - cx) + abs(ey - cy)):
        if tx < ty:
            cx += sx
            tx += tdx
        elif ty < tx:
            cy += sy
            ty += tdy
        else:  # through a corner: both neighbours count as touched
            yield cx + sx, cy
            yield cx, cy + sy
            cx += sx
            cy += sy
            tx += tdx
            ty += tdy
        yield cx, cy


def _clips(ax: float, ay: float, bx: float, by: float, x0: float, y0: float, size: float,
           eps: float = 1e-6) -> bool:
    """Does the segment touch the closed square [x0, x0+size] x [y0, y0+size]?
    (Liang-Barsky clipping.)"""
    t0, t1 = 0.0, 1.0
    dx, dy = bx - ax, by - ay
    for p, q in ((-dx, ax - (x0 - eps)), (dx, x0 + size + eps - ax),
                 (-dy, ay - (y0 - eps)), (dy, y0 + size + eps - ay)):
        if p == 0:
            if q < 0:
                return False
            continue
        r = q / p
        if p < 0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
        if t0 > t1:
            return False
    return True


def touched(ax: float, ay: float, bx: float, by: float, size: float) -> set[tuple[int, int]]:
    """Cells whose closed square the segment touches, borders included: a
    segment running exactly along a cell border touches the cells on both
    sides."""
    out = set()
    for cx, cy in set(_traverse(ax, ay, bx, by, size)):
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                c = (cx + i, cy + j)
                if c not in out and _clips(ax, ay, bx, by, c[0] * size, c[1] * size, size):
                    out.add(c)
    return out


def cells_of(grid: dict) -> list[tuple[int, int]]:
    out = []
    for cy, spans in grid["rows"]:
        for x0, x1 in spans:
            out += [(cx, cy) for cx in range(x0, x1 + 1)]
    return out


def cell_facts(grid: dict, parts_ll: list, shore: dict, lat0: float, lng0: float,
               ) -> tuple[list[int | None], list[bool]]:
    """(water distance in decimetres or None, edge flag) per cell, in cell order."""
    from .grid import to_grid
    anchor = grid["anchor"]
    size = grid["cell_size_m"]
    half = size / 2

    def g(x: float, y: float) -> tuple[float, float]:
        lat, lng = from_local(x, y, lat0, lng0)
        return to_grid(lat, lng, anchor)

    edge_cells: set[tuple[int, int]] = set()
    for outers, holes in parts_ll:
        for ring in outers + holes:
            pts = [to_grid(a, b, anchor) for a, b in ring]
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                edge_cells.update(touched(ax, ay, bx, by, size))
    source = shore["segments"] or shore["nearby"]
    segs = []
    for ax, ay, bx, by, kind in source:
        (gax, gay), (gbx, gby) = g(ax, ay), g(bx, by)
        segs.append((gax, gay, gbx, gby, kind))
    index = Segments(segs)
    cells = cells_of(grid)
    water: list[int | None] = []
    for cx, cy in cells:
        if not segs:
            water.append(None)
            continue
        x, y = cx * size + half, cy * size + half
        d, k = index.nearest(x, y, BUCKET_M)
        r = BUCKET_M
        while k < 0:
            r *= 2
            d, k = index.nearest(x, y, r)
        water.append(0 if d <= size else min(32767, round(d * 10)))
    return water, [(c in edge_cells) for c in cells]
