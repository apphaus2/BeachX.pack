"""Polygon helpers in the app's local metre frame. Stdlib only.

Local frame (the app's constants): east = (lng - lng0) * 111320 * cos(lat0),
north = (lat - lat0) * 110540. Rings are lists of (lat, lng), closed (first ==
last).
"""

from __future__ import annotations

import math

M_PER_DEG_LNG = 111320.0
M_PER_DEG_LAT = 110540.0

Point = tuple[float, float]  # (lat, lng)
Ring = list[Point]


def to_local(lat: float, lng: float, lat0: float, lng0: float) -> tuple[float, float]:
    return ((lng - lng0) * M_PER_DEG_LNG * math.cos(math.radians(lat0)),
            (lat - lat0) * M_PER_DEG_LAT)


def from_local(east: float, north: float, lat0: float, lng0: float) -> Point:
    return (lat0 + north / M_PER_DEG_LAT,
            lng0 + east / (M_PER_DEG_LNG * math.cos(math.radians(lat0))))


def clean_ring(points: list[Point]) -> Ring | None:
    """Drop consecutive duplicates and close. None if fewer than 3 distinct points."""
    out: Ring = []
    for p in points:
        if not out or p != out[-1]:
            out.append(p)
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    if len(set(out)) < 3:
        return None
    return out + [out[0]]


def join_ways(ways: list[list[Point]]) -> tuple[list[Ring], int]:
    """Join way segments that share end points into closed rings.

    Returns (closed rings, number of pieces that never closed).
    """
    pending = [list(w) for w in ways if len(w) >= 2]
    rings: list[Ring] = []
    unclosed = 0
    while pending:
        current = pending.pop(0)
        while current[0] != current[-1]:
            for i, other in enumerate(pending):
                if other[0] == current[-1]:
                    current += other[1:]
                elif other[-1] == current[-1]:
                    current += other[-2::-1]
                elif other[-1] == current[0]:
                    current = other[:-1] + current
                elif other[0] == current[0]:
                    current = other[:0:-1] + current
                else:
                    continue
                pending.pop(i)
                break
            else:
                break
        ring = clean_ring(current) if current[0] == current[-1] else None
        if ring:
            rings.append(ring)
        else:
            unclosed += 1
    return rings, unclosed


def signed_area_m2(ring: Ring, lat0: float, lng0: float) -> float:
    """Shoelace area in local metres. Positive when counter-clockwise."""
    pts = [to_local(lat, lng, lat0, lng0) for lat, lng in ring]
    total = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        total += x1 * y2 - x2 * y1
    return total / 2.0


def polygon_area_m2(outers: list[Ring], inners: list[Ring], lat0: float, lng0: float) -> float:
    area = sum(abs(signed_area_m2(r, lat0, lng0)) for r in outers)
    area -= sum(abs(signed_area_m2(r, lat0, lng0)) for r in inners)
    return max(area, 0.0)


def _seg_dist(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == 0 and dy == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def douglas_peucker(points: list[tuple[float, float]], tolerance: float) -> list[int]:
    """Indices kept by Douglas-Peucker on an open path (iterative, no recursion)."""
    n = len(points)
    if n <= 2:
        return list(range(n))
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        lo, hi = stack.pop()
        best, index = -1.0, -1
        for i in range(lo + 1, hi):
            d = _seg_dist(points[i], points[lo], points[hi])
            if d > best:
                best, index = d, i
        if index >= 0 and best > tolerance:
            keep[index] = True
            stack += [(lo, index), (index, hi)]
    return [i for i in range(n) if keep[i]]


def simplify_ring(ring: Ring, tolerance_m: float, lat0: float, lng0: float) -> Ring:
    """Douglas-Peucker on a closed ring in local metres.

    The ring is split at its first point and the point farthest from it, so
    both halves are open paths. Falls back to the input if the result would
    have fewer than 3 distinct points.
    """
    pts = [to_local(lat, lng, lat0, lng0) for lat, lng in ring]
    if len(pts) <= 4:
        return list(ring)
    far = max(range(len(pts) - 1), key=lambda i: math.hypot(pts[i][0] - pts[0][0],
                                                            pts[i][1] - pts[0][1]))
    first = douglas_peucker(pts[:far + 1], tolerance_m)
    second = [far + i for i in douglas_peucker(pts[far:], tolerance_m)]
    kept = first + second[1:]
    out = [ring[i] for i in kept]
    return out if len(out) >= 4 else list(ring)


def centroid(outers: list[Ring], lat0: float, lng0: float) -> Point:
    """Area-weighted centroid of the outer rings (holes ignored)."""
    sx = sy = sa = 0.0
    for ring in outers:
        pts = [to_local(lat, lng, lat0, lng0) for lat, lng in ring]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            cross = x1 * y2 - x2 * y1
            sa += cross
            sx += (x1 + x2) * cross
            sy += (y1 + y2) * cross
    if abs(sa) < 1e-9:
        pts = [p for r in outers for p in r[:-1]]
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    return from_local(sx / (3 * sa), sy / (3 * sa), lat0, lng0)


def point_in_ring(lat: float, lng: float, ring: Ring) -> bool:
    """Ray casting toward +lng."""
    inside = False
    for (y1, x1), (y2, x2) in zip(ring, ring[1:]):
        if (y1 > lat) != (y2 > lat):
            if lng < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
    return inside


def point_in_polygon(lat: float, lng: float, outers: list[Ring], inners: list[Ring]) -> bool:
    return (any(point_in_ring(lat, lng, r) for r in outers)
            and not any(point_in_ring(lat, lng, r) for r in inners))


def interior_point(outers: list[Ring], inners: list[Ring]) -> Point:
    """A point guaranteed inside the polygon (for per-point lookups).

    Scan the latitude line through the middle of the largest outer ring's
    box, and take the middle of the widest inside interval.
    """
    ring = max(outers, key=len)
    lats = [p[0] for p in ring]
    for frac in (0.5, 0.25, 0.75, 0.4, 0.6, 0.1, 0.9):
        lat = min(lats) + (max(lats) - min(lats)) * frac
        xs = sorted(x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
                    for r in outers + inners for (y1, x1), (y2, x2) in zip(r, r[1:])
                    if (y1 > lat) != (y2 > lat))
        pairs = list(zip(xs[0::2], xs[1::2]))
        if pairs:
            a, b = max(pairs, key=lambda ab: ab[1] - ab[0])
            if b > a:
                return (lat, (a + b) / 2)
    return ring[0]
