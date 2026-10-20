"""Small hand-made Overpass payloads for offline tests."""

from __future__ import annotations

import math

from beachx_pack.geometry import from_local

LAT0, LNG0 = 21.2767, -157.8278  # Waikiki


def rect(length: float, width: float, heading_deg: float, lat0: float = LAT0,
         lng0: float = LNG0, offset: tuple[float, float] = (0, 0)) -> list[tuple[float, float]]:
    """Closed (lat, lng) ring: a rectangle whose long side points along heading."""
    h = math.radians(heading_deg)
    u = (math.sin(h), math.cos(h))     # along heading (east, north)
    v = (math.cos(h), -math.sin(h))    # across
    pts = []
    for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1), (-1, -1)):
        e = offset[0] + a * length / 2 * u[0] + b * width / 2 * v[0]
        n = offset[1] + a * length / 2 * u[1] + b * width / 2 * v[1]
        pts.append(from_local(e, n, lat0, lng0))
    return pts


def geom(points: list[tuple[float, float]]) -> list[dict]:
    return [{"lat": a, "lon": b} for a, b in points]


def bounds(points: list[tuple[float, float]]) -> dict:
    return {"minlat": min(p[0] for p in points), "minlon": min(p[1] for p in points),
            "maxlat": max(p[0] for p in points), "maxlon": max(p[1] for p in points)}


def way(osm_id: int, points, tags: dict) -> dict:
    return {"type": "way", "id": osm_id, "bounds": bounds(points), "geometry": geom(points),
            "tags": tags}


def marker(code: str, members: list[str], found: bool = True) -> dict:
    return {"type": "beachx_area", "id": 1, "tags": {
        "code": code, "area_ids": "3600000001" if found else "", "members": ";".join(members)}}


def tile(tile_id: str, elements: list[dict], state: str = "HI", country: str = "US") -> dict:
    return {"tile": {"id": tile_id, "bbox": [20.5, -158.5, 22.5, -156.5], "state": state,
                     "country": country, "region": "hawaii"},
            "response": {"elements": elements}}
