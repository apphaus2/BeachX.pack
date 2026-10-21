"""Water near beaches: the OSM coastline and water polygons.

A third Overpass query per tile (raw/<run-date>/osm_water/) returns every
`natural=coastline` way and every water polygon (`natural=water`,
`waterway=riverbank`) within SEARCH_M of a beach in the tile, with full
geometry. The build finds the stretch of each beach's boundary that meets
the water (the water edge), turns the grid so +y points up the beach, and
measures each cell's distance to the water edge.
"""

from __future__ import annotations

SEARCH_M = 500
RIVER = {"river", "canal", "stream", "riverbank", "ditch", "drain", "oxbow", "rapids"}
SEA_WATER = {"lagoon", "harbour", "bay", "cove", "tidal_pool", "sea", "strait", "fjord"}
OPENS_TO_SEA_M = 10.0  # a water polygon this close to the coastline opens to the sea


def build_query(bbox: tuple[float, float, float, float]) -> str:
    """Beaches in the tile, then coastline and water polygons near them, then
    the same `around` canary as the nearby query."""
    s, w, n, e = bbox
    box = f"({s},{w},{n},{e})"
    r = f"{SEARCH_M:g}"
    lines = [
        "[out:json][timeout:600];",
        f'(nwr["natural"="beach"]{box}; nwr["leisure"="beach_resort"]{box};)->.b;',
        "(",
        f'  way["natural"="coastline"](around.b:{r});',
        f'  way["natural"="water"](around.b:{r});',
        f'  relation["natural"="water"](around.b:{r});',
        f'  way["waterway"="riverbank"](around.b:{r});',
        f'  relation["waterway"="riverbank"](around.b:{r});',
        ");",
        "out geom qt;",
        ".b out count;",
        'nwr["natural"="beach"](around.b:1);',
        "out count;",
    ]
    return "\n".join(lines) + "\n"


def kind_of(tags: dict) -> str | None:
    """sea (coastline, or salt or tidal water), lake, or river. A lake
    polygon that touches the coastline is turned into sea later
    (normalize.WaterIndex), since it opens to the sea."""
    if tags.get("natural") == "coastline":
        return "sea"
    if tags.get("water") in SEA_WATER or tags.get("salt") == "yes" or tags.get("tidal") == "yes":
        return "sea"
    if tags.get("waterway") == "riverbank" or tags.get("water") in RIVER:
        return "river"
    if tags.get("natural") == "water":
        return "lake"
    return None
