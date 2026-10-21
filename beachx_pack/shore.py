"""Water edge, orientation, and per-cell water distance and edge flags.

Everything here works in local metres around the beach (geometry.to_local)
and then in the grid frame (grid.to_grid).

- Which side is water comes from the water itself, never from the beach
  outline: the OSM coastline keeps land on its left, and a water polygon's
  water is inside it. (Many OSM beaches are drawn seaward of the coastline,
  between high and low water; for those the outline meets the coastline on
  its landward side.)
- Water edge: the boundary segments with water on their outside. A segment
  is water edge when, for at least half of its sample points (every
  SAMPLE_M), a point PROBE_M outward (away from the beach) is in the water:
  on the coastline's water side or inside a water polygon. Consecutive
  segments form one polyline. A beach drawn landward of the coastline gets
  its seaward side; one drawn seaward of it (between high and low water)
  also gets its seaward side, never the side along the coastline.
- Orientation: the water-side normals of the coastline and water outlines
  within NEAR_M of the beach, weighted by length, point to the water; +y
  (the grid heading) is the opposite direction: up the beach. Without water
  that close, the direction from the beach's centre to the nearest water
  within water.SEARCH_M stands in; without any water, +y is across the
  outline's long axis and its sign is arbitrary (orientation "outline").
- Water distance: from each cell centre to the nearest water-edge segment
  (to the nearest coastline or water outline when the beach has no water
  edge), 0 within one cell size of it, None when there is no water at all.
- Edge cells: cells whose square a boundary segment crosses.
"""

from __future__ import annotations

import math

from .geometry import Ring, from_local, to_local

EDGE_TOL_M = 10.0
PROBE_M = (1.0, 5.0, EDGE_TOL_M)  # outward probes for the water edge
NEAR_M = 60.0
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

class Water:
    """The water near one beach in local metres: directed coastline segments
    (land on the left), water polygons, and every outline segment."""

    def __init__(self, features: list[dict], lat0: float, lng0: float, convert=None):
        convert = convert or (lambda a, b: to_local(a, b, lat0, lng0))
        self.coast: list[tuple] = []  # (ax, ay, bx, by, kind, left_turn_at_a, left_turn_at_b)
        self.polys: list[tuple] = []  # (outers, inners, kind, bbox)
        outline: list[tuple] = []
        for f in features:
            if f.get("coastline"):
                for line in f["lines"]:
                    pts = [convert(a, b) for a, b in line]
                    n = len(pts) - 1
                    for k in range(n):
                        (ax, ay), (bx, by) = pts[k], pts[k + 1]
                        turn_a = _left_turn(pts[k - 1], pts[k], pts[k + 1]) if k > 0 else None
                        turn_b = _left_turn(pts[k], pts[k + 1], pts[k + 2]) if k + 1 < n else None
                        self.coast.append((ax, ay, bx, by, f["kind"], turn_a, turn_b))
            else:
                for o, i in f["polygons"]:
                    lo = [[convert(a, b) for a, b in r] for r in o]
                    li = [[convert(a, b) for a, b in r] for r in i]
                    xs = [p[0] for r in lo for p in r]
                    ys = [p[1] for r in lo for p in r]
                    self.polys.append((lo, li, f["kind"], (min(xs), min(ys), max(xs), max(ys))))
                for line in f["lines"]:
                    pts = [convert(a, b) for a, b in line]
                    outline += [(p[0], p[1], q[0], q[1], f["kind"]) for p, q in zip(pts, pts[1:])]
        self.coast_index = Segments([c[:5] for c in self.coast])
        self.all = Segments([c[:5] for c in self.coast] + outline)

    def empty(self) -> bool:
        return not self.all.segs

    def polygon_at(self, x: float, y: float) -> str | None:
        for o, i, kind, (x0, y0, x1, y1) in self.polys:
            if x0 <= x <= x1 and y0 <= y <= y1 and any(_in_ring(x, y, r) for r in o) \
                    and not any(_in_ring(x, y, h) for h in i):
                return kind
        return None

    def sea_side(self, x: float, y: float, reach: float) -> str | None:
        """The coastline's kind when the point is on its water (right) side."""
        d, k = self.coast_index.nearest(x, y, reach)
        if k < 0:
            return None
        ax, ay, bx, by, kind, turn_a, turn_b = self.coast[k]
        dx, dy = bx - ax, by - ay
        t = 0.0 if dx == dy == 0 else ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)
        if t <= 0 and turn_a is not None:  # nearest is the vertex: outside a left turn is water
            return kind if turn_a else None
        if t >= 1 and turn_b is not None:
            return kind if turn_b else None
        return kind if dx * (y - ay) - dy * (x - ax) < 0 else None

    def at(self, x: float, y: float, reach: float = 600.0) -> str | None:
        """The water kind when the point is in the water."""
        return self.polygon_at(x, y) or self.sea_side(x, y, reach)

    def side_normals(self, near) -> tuple[float, float, float]:
        """Sum of water-side unit normals times length over coastline and
        outline segments that `near(mx, my)` accepts, and the total length."""
        sx = sy = total = 0.0
        for ax, ay, bx, by, kind, *_ in self.coast:
            mx, my = (ax + bx) / 2, (ay + by) / 2
            seg = math.hypot(bx - ax, by - ay)
            if seg and near(mx, my):
                sx += (by - ay)  # right-hand normal times length
                sy += -(bx - ax)
                total += seg
        for o, i, kind, _ in self.polys:
            for ring in o + i:
                for (ax, ay), (bx, by) in zip(ring, ring[1:]):
                    mx, my = (ax + bx) / 2, (ay + by) / 2
                    seg = math.hypot(bx - ax, by - ay)
                    if not seg or not near(mx, my):
                        continue
                    nx, ny = (by - ay) / seg, -(bx - ax) / seg
                    if not self.polygon_at(mx + 0.5 * nx, my + 0.5 * ny):
                        nx, ny = -nx, -ny  # point into the water
                    sx += nx * seg
                    sy += ny * seg
                    total += seg
        return sx, sy, total


def _left_turn(p, q, r) -> bool:
    return (q[0] - p[0]) * (r[1] - q[1]) - (q[1] - p[1]) * (r[0] - q[0]) > 0


def water_edge(parts_ll: list, features: list[dict], lat0: float, lng0: float,
               override: list | None = None) -> dict:
    """The boundary stretch that meets the water, and which side is the water.

    parts_ll: [(outers, holes)] of (lat, lng) rings. Returns {"edge": polylines
    of [lat, lng], "kind", "length_m", "seaward": (east, north) or None,
    "orientation", "features": the water used for distances, "override":
    local segments of an override water edge}.
    """
    parts = [([[to_local(a, b, lat0, lng0) for a, b in r] for r in o],
              [[to_local(a, b, lat0, lng0) for a, b in r] for r in i]) for o, i in parts_ll]
    water = Water(features, lat0, lng0)
    boundary = Segments([(p[0], p[1], q[0], q[1], "") for o, i in parts for r in o + i
                         for p, q in zip(r, r[1:])])

    def meets(x: float, y: float, nx: float, ny: float) -> str | None:
        """Water kind found PROBE_M outward of an outline point."""
        for d in PROBE_M:
            kind = water.at(x + nx * d, y + ny * d)
            if kind:
                return kind
        return None

    edge_ll: list[list[list[float]]] = []
    edge_segs: list[tuple] = []
    by_kind: dict[str, float] = {}
    over: list[tuple] = []
    length = 0.0
    if override is not None:
        for line in override:
            loc = [to_local(a, b, lat0, lng0) for a, b in line]
            edge_ll.append([list(p) for p in line])
            over += [(p[0], p[1], q[0], q[1], "override") for p, q in zip(loc, loc[1:])]
        length = sum(math.hypot(s[2] - s[0], s[3] - s[1]) for s in over)
    else:
        for (outers, holes), (o_ll, h_ll) in zip(parts, parts_ll):
            for ring, ring_ll in zip(outers + holes, o_ll + h_ll):
                n = len(ring) - 1
                flags: list[str | None] = []
                for k in range(n):
                    (ax, ay), (bx, by) = ring[k], ring[k + 1]
                    seg = math.hypot(bx - ax, by - ay)
                    if seg == 0:
                        flags.append(None)
                        continue
                    nx, ny = (by - ay) / seg, -(bx - ax) / seg
                    mx, my = (ax + bx) / 2, (ay + by) / 2
                    if _in_parts(mx + 0.5 * nx, my + 0.5 * ny, parts):  # point it outward
                        nx, ny = -nx, -ny
                    steps = max(2, math.ceil(seg / SAMPLE_M))
                    hits = [meets(ax + (bx - ax) * t / steps, ay + (by - ay) * t / steps, nx, ny)
                            for t in range(steps + 1)]
                    good = [h for h in hits if h]
                    flags.append(max(sorted(set(good)), key=good.count)
                                 if 2 * len(good) >= len(hits) else None)
                if not any(flags):
                    continue
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
                        seg = math.hypot(bx - ax, by - ay)
                        length += seg
                        by_kind[flags[k]] = by_kind.get(flags[k], 0.0) + seg
                        edge_segs.append((ax, ay, bx, by, flags[k]))
    out = {"edge": [[[round(a, 7), round(b, 7)] for a, b in line] for line in edge_ll],
           "length_m": round(length, 1), "kind": None, "seaward": None,
           "orientation": "outline", "features": features, "override": over,
           "edge_segments": edge_segs}
    if by_kind:
        out["kind"] = max(sorted(by_kind), key=by_kind.get)
    pts = [p for o, i in parts for r in o for p in r[:-1]]
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    if over:
        # an override edge: the water is on its side away from the beach's centre
        sx = sy = 0.0
        for ax, ay, bx, by, _ in over:
            nx, ny = by - ay, -(bx - ax)
            if nx * ((ax + bx) / 2 - cx) + ny * ((ay + by) / 2 - cy) < 0:
                nx, ny = -nx, -ny
            sx, sy = sx + nx, sy + ny
        out["seaward"] = (sx, sy) if math.hypot(sx, sy) > 0 else None
        out["orientation"] = "override"
        return out
    if water.empty():
        return out
    sx, sy, total = water.side_normals(
        lambda mx, my: boundary.nearest(mx, my, NEAR_M)[1] >= 0)
    if total and math.hypot(sx, sy) >= AMBIGUOUS * total:
        out["seaward"] = (sx, sy)
        out["orientation"] = "water"
    # no water close by, or no clear side: toward the nearest water
    best = (math.inf, -1, None)
    from .water import SEARCH_M
    for x, y in pts + [(cx, cy)]:
        d, k = water.all.nearest(x, y, SEARCH_M)
        if d < best[0]:
            best = (d, k, (x, y))
    if best[1] >= 0:
        ax, ay, bx, by, kind = water.all.segs[best[1]]
        if out["kind"] is None:
            out["kind"] = kind
        if out["seaward"] is None:
            x, y = best[2]
            dx, dy = bx - ax, by - ay
            t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy)
                                                       / (dx * dx + dy * dy)))
            vx, vy = ax + t * dx - cx, ay + t * dy - cy
            if math.hypot(vx, vy) > 0:
                out["seaward"] = (vx, vy)
                out["orientation"] = "nearby_water" if not total else "ambiguous_nearby"
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
    edge_cells: set[tuple[int, int]] = set()
    for outers, holes in parts_ll:
        for ring in outers + holes:
            pts = [to_grid(a, b, anchor) for a, b in ring]
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                edge_cells.update(touched(ax, ay, bx, by, size))
    cells = cells_of(grid)
    edge = shore["override"] or shore.get("edge_segments") or []
    if edge:
        segs = Segments([(*to_grid(*from_local(ax, ay, lat0, lng0), anchor),
                          *to_grid(*from_local(bx, by, lat0, lng0), anchor), k)
                         for ax, ay, bx, by, k in edge])
    else:  # no water edge: the nearest water itself
        segs = Water(shore["features"], lat0, lng0, convert=lambda a, b: to_grid(a, b, anchor)).all
    out: list[int | None] = []
    for cx, cy in cells:
        if not segs.segs:
            out.append(None)
            continue
        x, y = cx * size + half, cy * size + half
        d, k = segs.nearest(x, y, BUCKET_M)
        r = BUCKET_M
        while k < 0:
            r *= 2
            d, k = segs.nearest(x, y, r)
        out.append(0 if d <= size else min(32767, round(d * 10)))
    return out, [(c in edge_cells) for c in cells]
