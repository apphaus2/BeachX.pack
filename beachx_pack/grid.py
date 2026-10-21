"""Official 5 m beach grids, in the app's Dart frame.

anchor   = area-weighted centroid of the simplified outer ring(s)
heading  = first principal axis of the ring points (PCA), degrees clockwise
           from true north, in [0, 180), rounded to 0.1
frame    = east/north metres from the anchor (geometry.to_local), rotated:
           x = east*cos(h) - north*sin(h);  y = east*sin(h) + north*cos(h)
cells    = (cx, cy) = (floor(x/5), floor(y/5)); a cell is in when its centre
           lies inside the outer ring(s) and outside every hole
"""

from __future__ import annotations

import math

from .geometry import Ring, centroid, from_local, to_local

CELL_SIZE_M = 5
ANCHOR_DECIMALS = 7


def principal_heading(points_en: list[tuple[float, float]]) -> float:
    """Long-axis direction of (east, north) points as degrees in [0, 180)."""
    n = len(points_en)
    mx = sum(p[0] for p in points_en) / n
    my = sum(p[1] for p in points_en) / n
    sxx = sum((p[0] - mx) ** 2 for p in points_en)
    syy = sum((p[1] - my) ** 2 for p in points_en)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in points_en)
    theta = 0.5 * math.atan2(2 * sxy, sxx - syy)  # from east, counter-clockwise
    heading = math.degrees(math.atan2(math.cos(theta), math.sin(theta)))  # from north, cw
    heading = round(heading % 180.0, 1)
    return 0.0 if heading >= 180.0 else heading


def to_grid(lat: float, lng: float, anchor: dict) -> tuple[float, float]:
    east, north = to_local(lat, lng, anchor["latitude"], anchor["longitude"])
    h = math.radians(anchor["heading_degrees"])
    return (east * math.cos(h) - north * math.sin(h), east * math.sin(h) + north * math.cos(h))


def from_grid(x: float, y: float, anchor: dict) -> tuple[float, float]:
    h = math.radians(anchor["heading_degrees"])
    east = x * math.cos(h) + y * math.sin(h)
    north = -x * math.sin(h) + y * math.cos(h)
    return from_local(east, north, anchor["latitude"], anchor["longitude"])


def make_anchor(outers: list[Ring]) -> dict:
    ref_lat, ref_lng = outers[0][0]
    lat, lng = centroid(outers, ref_lat, ref_lng)
    lat, lng = round(lat, ANCHOR_DECIMALS), round(lng, ANCHOR_DECIMALS)
    pts = [to_local(a, b, lat, lng) for ring in outers for a, b in ring[:-1]]
    return {"latitude": lat, "longitude": lng, "heading_degrees": principal_heading(pts)}


def _row_spans(polygon: tuple[list[Ring], list[Ring]], anchor: dict) -> dict[int, list[list[int]]]:
    """Inclusive [x0, x1] spans per row whose cell centres lie inside one
    polygon (its outer rings minus its holes, even-odd)."""
    outers, inners = polygon
    edges = []
    for ring in outers + inners:
        pts = [to_grid(lat, lng, anchor) for lat, lng in ring]
        edges += list(zip(pts, pts[1:]))
    half = CELL_SIZE_M / 2
    # Bucket each edge under the rows whose centre line it crosses, so long
    # beaches cost (edges + rows), not (edges x rows).
    buckets: dict[int, list[float]] = {}
    for (x1, y1), (x2, y2) in edges:
        if y1 == y2:
            continue
        lo, hi = min(y1, y2), max(y1, y2)
        for cy in range(math.ceil((lo - half) / CELL_SIZE_M),
                        math.floor((hi - half) / CELL_SIZE_M) + 1):
            yc = cy * CELL_SIZE_M + half
            if (y1 > yc) != (y2 > yc):
                buckets.setdefault(cy, []).append(x1 + (yc - y1) * (x2 - x1) / (y2 - y1))
    rows: dict[int, list[list[int]]] = {}
    for cy in sorted(buckets):
        xs = sorted(buckets[cy])
        spans: list[list[int]] = []
        for a, b in zip(xs[0::2], xs[1::2]):  # centre inside when a <= xc < b
            x0 = math.ceil((a - half) / CELL_SIZE_M)
            x1 = math.ceil((b - half) / CELL_SIZE_M) - 1
            if x0 > x1:
                continue
            if spans and x0 <= spans[-1][1] + 1:
                spans[-1][1] = max(spans[-1][1], x1)
            else:
                spans.append([x0, x1])
        if spans:
            rows[cy] = spans
    return rows


def _union(a: list[list[int]], b: list[list[int]]) -> list[list[int]]:
    out: list[list[int]] = []
    for x0, x1 in sorted(a + b):
        if out and x0 <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], x1)
        else:
            out.append([x0, x1])
    return out


def build_grid(outers: list[Ring], inners: list[Ring],
               polygons: list[tuple[list[Ring], list[Ring]]] | None = None) -> dict | None:
    """Run-length encoded cell set, or None when no cell centre falls inside.

    `polygons` (a merged beach) lists each member's (outers, holes); a cell is
    in when its centre lies inside any member. Without it, the one polygon
    is (outers, inners).
    """
    polygons = polygons or [(outers, inners)]
    anchor = make_anchor([r for o, _ in polygons for r in o])
    merged: dict[int, list[list[int]]] = {}
    for poly in polygons:
        for cy, spans in _row_spans(poly, anchor).items():
            merged[cy] = _union(merged.get(cy, []), spans)
    rows = [[cy, merged[cy]] for cy in sorted(merged)]
    if not rows:
        return None
    count = sum(x1 - x0 + 1 for _, spans in rows for x0, x1 in spans)
    return {
        "anchor": anchor,
        "cell_size_m": CELL_SIZE_M,
        "cell_count": count,
        "x_min": min(s[0] for _, spans in rows for s in spans),
        "x_max": max(s[1] for _, spans in rows for s in spans),
        "y_min": rows[0][0],
        "y_max": rows[-1][0],
        "rows": rows,
    }


def cell_centres(grid: dict) -> list[tuple[float, float]]:
    """(lat, lng) of every cell centre. For tests and spot checks."""
    half = grid["cell_size_m"] / 2
    out = []
    for cy, spans in grid["rows"]:
        for x0, x1 in spans:
            for cx in range(x0, x1 + 1):
                out.append(from_grid(cx * grid["cell_size_m"] + half,
                                     cy * grid["cell_size_m"] + half, grid["anchor"]))
    return out
