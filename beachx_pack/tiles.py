"""Hand-written coastal tiles and the state bounding-box fallback table."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Tile:
    id: str
    south: float
    west: float
    north: float
    east: float
    state: str  # primary state (USPS code), used only to break ties
    country: str  # US, PR, or VI
    region: str

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (self.south, self.west, self.north, self.east)


def _fmt(value: float, pos: str, neg: str) -> str:
    text = f"{abs(value):g}"
    return f"{text}{pos if value >= 0 else neg}"


def _tile(region: str, state: str, s: float, w: float, n: float, e: float,
          country: str = "US") -> Tile:
    tid = f"{state}_{_fmt(s, 'N', 'S')}_{_fmt(w, 'E', 'W')}"
    return Tile(tid, s, w, n, e, state, country, region)


def _row(region: str, s: float, cells: list[tuple[str, float]], height: float = 2,
         width: float = 2) -> list[Tile]:
    return [_tile(region, st, s, w, s + height, w + width) for st, w in cells]


def _alaska() -> list[Tile]:
    # Alaska uses 2 deg of latitude by 4 deg of longitude: at 51-72 N that is
    # about the same ground size as a 2x2 tile further south.
    rows: dict[float, list[float]] = {
        51: [172, 176, -180, -176, -172],
        53: [-172, -168, -164, -160, -136, -132],
        55: [-172, -164, -160, -156, -136, -132],
        57: [-172, -164, -160, -156, -140, -136],
        59: [-168, -164, -156, -152, -148, -144, -140, -136],
        61: [-168, -164, -152, -148],
        63: [-172, -168, -164],
        65: [-172, -168, -164],
        67: [-168, -164],
        69: [-168, -164, -160, -156, -152, -148, -144],
        71: [-160, -156],
    }
    out: list[Tile] = []
    for s, wests in rows.items():
        out += _row("alaska", s, [("AK", w) for w in wests], width=4)
    return out


TILES: list[Tile] = [
    # Hawaii: main islands only (Northwestern Hawaiian Islands are skipped).
    _tile("hawaii", "HI", 20.5, -160.5, 22.5, -158.5),
    _tile("hawaii", "HI", 20.5, -158.5, 22.5, -156.5),
    _tile("hawaii", "HI", 20.5, -156.5, 22.5, -154.5),
    _tile("hawaii", "HI", 18.5, -156.5, 20.5, -154.5),
    # Atlantic, south to north.
    *_row("atlantic", 24, [("FL", -81)]),
    *_row("atlantic", 26, [("FL", -81)]),
    *_row("atlantic", 28, [("FL", -82)]),
    *_row("atlantic", 30, [("FL", -82)]),
    *_row("atlantic", 32, [("SC", -82), ("SC", -80), ("NC", -78)]),
    *_row("atlantic", 34, [("NC", -79), ("NC", -77)]),
    *_row("atlantic", 36, [("VA", -77)]),
    *_row("atlantic", 38, [("MD", -77), ("NJ", -75)]),
    *_row("atlantic", 40, [("NY", -75), ("CT", -73), ("MA", -71)]),
    *_row("atlantic", 42, [("MA", -71), ("ME", -69)]),
    *_row("atlantic", 44, [("ME", -71), ("ME", -69), ("ME", -67)]),
    # Gulf of Mexico, Florida Keys to Texas.
    *_row("gulf", 24, [("FL", -83)]),
    *_row("gulf", 26, [("FL", -83)]),
    *_row("gulf", 28, [("FL", -84), ("LA", -90), ("TX", -96)]),
    *_row("gulf", 29, [("FL", -84), ("FL", -86), ("FL", -88), ("MS", -90),
                       ("LA", -92), ("LA", -94)]),
    *_row("gulf", 27, [("TX", -98)]),
    *_row("gulf", 25, [("TX", -98)]),
    # Pacific, south to north.
    *_row("pacific", 32, [("CA", -119), ("CA", -121)]),
    *_row("pacific", 34, [("CA", -119), ("CA", -121), ("CA", -123)]),
    *_row("pacific", 36, [("CA", -123), ("CA", -125)]),
    *_row("pacific", 38, [("CA", -125), ("CA", -123)]),
    *_row("pacific", 40, [("CA", -125)]),
    *_row("pacific", 42, [("OR", -125)]),
    *_row("pacific", 44, [("OR", -125)]),
    *_row("pacific", 46, [("WA", -125), ("WA", -123)]),
    *_row("pacific", 48, [("WA", -125), ("WA", -123)]),
    # Great Lakes.
    *_row("great_lakes", 41, [("IL", -89), ("IN", -87), ("MI", -85), ("OH", -83),
                              ("PA", -81), ("NY", -79)]),
    *_row("great_lakes", 43, [("WI", -89), ("MI", -87), ("MI", -85), ("MI", -83),
                              ("NY", -79), ("NY", -77)]),
    *_row("great_lakes", 45, [("MN", -93), ("WI", -91), ("MI", -89), ("MI", -87),
                              ("MI", -85)]),
    *_row("great_lakes", 47, [("MN", -93), ("MN", -91), ("MI", -89)]),
    *_alaska(),
    # Territories.
    _tile("territories", "PR", 17.5, -68.0, 18.7, -66.4, country="PR"),
    _tile("territories", "PR", 17.5, -66.4, 18.7, -65.15, country="PR"),
    _tile("territories", "VI", 17.5, -65.15, 18.5, -64.4, country="VI"),
]

REGIONS = ("hawaii", "alaska", "atlantic", "gulf", "pacific", "great_lakes", "territories")

# Overpass area filters used to drop foreign beaches that fall inside a tile.
COUNTRY_AREA = {
    "US": '["ISO3166-1"="US"]["admin_level"="2"]',
    "PR": '["ISO3166-2"="US-PR"]',
    "VI": '["ISO3166-2"="US-VI"]',
}

# Fallback state boxes: (south, west, north, east). Hand-entered approximations of
# US Census state extents, then widened by MARGIN on every side. Used only when
# OSM area membership and tags give no answer (state_confidence "bbox").
MARGIN = 0.1
STATE_BOXES: dict[str, list[tuple[float, float, float, float]]] = {
    "AL": [(30.14, -88.47, 35.01, -84.89)],
    "AK": [(51.20, -180.0, 71.50, -129.98), (51.20, 172.40, 53.10, 180.0)],
    "CA": [(32.53, -124.41, 42.01, -114.13)],
    "CT": [(40.98, -73.73, 42.05, -71.79)],
    "DE": [(38.45, -75.79, 39.84, -75.05)],
    "DC": [(38.79, -77.12, 39.00, -76.91)],
    "FL": [(24.40, -87.63, 31.00, -79.97)],
    "GA": [(30.36, -85.61, 35.00, -80.84)],
    "HI": [(18.91, -178.40, 28.45, -154.81)],
    "IL": [(36.97, -91.51, 42.51, -87.02)],
    "IN": [(37.77, -88.10, 41.76, -84.78)],
    "LA": [(28.93, -94.04, 33.02, -88.82)],
    "ME": [(43.06, -71.08, 47.46, -66.95)],
    "MD": [(37.91, -79.49, 39.72, -75.05)],
    "MA": [(41.24, -73.51, 42.89, -69.93)],
    "MI": [(41.70, -90.42, 48.31, -82.41)],
    "MN": [(43.50, -97.24, 49.38, -89.49)],
    "MS": [(30.17, -91.66, 35.00, -88.10)],
    "NH": [(42.70, -72.56, 45.31, -70.61)],
    "NJ": [(38.93, -75.56, 41.36, -73.89)],
    "NY": [(40.50, -79.76, 45.02, -71.86)],
    "NC": [(33.84, -84.32, 36.59, -75.46)],
    "OH": [(38.40, -84.82, 42.33, -80.52)],
    "OR": [(41.99, -124.57, 46.29, -116.46)],
    "PA": [(39.72, -80.52, 42.27, -74.69)],
    "RI": [(41.15, -71.91, 42.02, -71.12)],
    "SC": [(32.03, -83.35, 35.22, -78.54)],
    "TX": [(25.84, -106.65, 36.50, -93.51)],
    "VA": [(36.54, -83.68, 39.47, -75.24)],
    "WA": [(45.54, -124.85, 49.00, -116.92)],
    "WI": [(42.49, -92.89, 47.31, -86.25)],
    "PR": [(17.88, -67.95, 18.52, -65.22)],
    "VI": [(17.67, -65.09, 18.42, -64.56)],
    # Inland states, from the TIGER/Line 2024 state polygons' extents.
    "AR": [(33.00, -94.62, 36.50, -89.64)],
    "AZ": [(31.33, -114.82, 37.00, -109.05)],
    "CO": [(36.99, -109.06, 41.00, -102.04)],
    "IA": [(40.38, -96.64, 43.50, -90.14)],
    "ID": [(41.99, -117.24, 49.00, -111.04)],
    "KS": [(36.99, -102.05, 40.00, -94.59)],
    "KY": [(36.50, -89.57, 39.15, -81.96)],
    "MO": [(36.00, -95.77, 40.61, -89.10)],
    "MT": [(44.36, -116.05, 49.00, -104.04)],
    "ND": [(45.94, -104.05, 49.00, -96.55)],
    "NE": [(40.00, -104.05, 43.00, -95.31)],
    "NM": [(31.33, -109.05, 37.00, -103.00)],
    "NV": [(35.00, -120.01, 42.00, -114.04)],
    "OK": [(33.62, -103.00, 37.00, -94.43)],
    "SD": [(42.48, -104.06, 45.95, -96.44)],
    "TN": [(34.98, -90.31, 36.68, -81.65)],
    "UT": [(37.00, -114.05, 42.00, -109.04)],
    "VT": [(42.73, -73.44, 45.02, -71.47)],
    "WV": [(37.20, -82.64, 40.64, -77.72)],
    "WY": [(40.99, -111.05, 45.01, -104.05)],
}

STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "california": "CA", "connecticut": "CT",
    "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "hawaiʻi": "HI", "illinois": "IL", "indiana": "IN",
    "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "new hampshire": "NH",
    "new jersey": "NJ", "new york": "NY", "north carolina": "NC", "ohio": "OH",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "texas": "TX", "virginia": "VA", "washington": "WA", "wisconsin": "WI",
    "puerto rico": "PR", "u.s. virgin islands": "VI", "us virgin islands": "VI",
    "virgin islands": "VI", "arkansas": "AR", "arizona": "AZ", "colorado": "CO", "iowa": "IA",
    "idaho": "ID", "kansas": "KS", "kentucky": "KY", "missouri": "MO", "montana": "MT",
    "north dakota": "ND", "nebraska": "NE", "new mexico": "NM", "nevada": "NV",
    "oklahoma": "OK", "south dakota": "SD", "tennessee": "TN", "utah": "UT", "vermont": "VT",
    "west virginia": "WV", "wyoming": "WY",
}

COUNTRY_OF_STATE = {"PR": "PR", "VI": "VI"}


def _boxes(code: str) -> list[tuple[float, float, float, float]]:
    return [(s - MARGIN, w - MARGIN, n + MARGIN, e + MARGIN)
            for s, w, n, e in STATE_BOXES[code]]


def states_for_point(lat: float, lon: float) -> list[str]:
    """State codes whose (widened) box contains the point, sorted."""
    return sorted(code for code in STATE_BOXES
                  if any(s <= lat <= n and w <= lon <= e for s, w, n, e in _boxes(code)))


def states_for_tile(tile: Tile) -> list[str]:
    """State codes whose box intersects the tile; the tile's own state first."""
    hits = {code for code in STATE_BOXES
            if any(s <= tile.north and n >= tile.south and w <= tile.east and e >= tile.west
                   for s, w, n, e in _boxes(code))}
    if tile.country != "US":
        hits = {tile.state}
    hits.discard("DC")
    return [tile.state] + sorted(hits - {tile.state})


def state_code(value: str | None) -> str | None:
    """Turn an addr:state style value into a known USPS code, else None."""
    if not value:
        return None
    text = value.strip()
    if text.upper() in STATE_BOXES:
        return text.upper()
    return STATE_NAMES.get(text.lower())


def select_tiles(spec: str) -> list[Tile]:
    """`all`, a region name, or a comma list of regions and state codes."""
    if spec.strip().lower() == "all":
        return list(TILES)
    wanted = [p.strip() for p in spec.split(",") if p.strip()]
    chosen: list[Tile] = []
    for tile in TILES:
        for part in wanted:
            if part.lower() in REGIONS and tile.region == part.lower():
                chosen.append(tile)
                break
            if part.upper() in STATE_BOXES and part.upper() in states_for_tile(tile):
                chosen.append(tile)
                break
    unknown = [p for p in wanted if p.lower() not in REGIONS and p.upper() not in STATE_BOXES]
    if unknown:
        raise ValueError(f"unknown tile selector(s): {', '.join(unknown)}")
    return chosen


TILE_DEG = 2.0
# Generated tiles are clipped to the state's box grown by this much, a little
# over the 2 km a beach outside every state may lie from its state.
TILE_PAD_DEG = 0.03


def generated_tiles(code: str, bbox: tuple[float, float, float, float]) -> list[Tile]:
    """2 x 2 degree tiles on even degrees over a state's box, for states
    with no hand-written coastal tiles (inland states). Each tile is clipped
    to the box grown by TILE_PAD_DEG, so a state that crosses an even degree
    by a few hundred metres (Kansas reaches 40.003 N) does not pull a whole
    row of its neighbour's tiles. The id keeps the even-degree corner."""
    s, w, n, e = bbox
    s, w, n, e = s - TILE_PAD_DEG, w - TILE_PAD_DEG, n + TILE_PAD_DEG, e + TILE_PAD_DEG
    country = COUNTRY_OF_STATE.get(code, "US")
    out = []
    lat = math.floor(s / TILE_DEG) * TILE_DEG
    while lat < n:
        lng = math.floor(w / TILE_DEG) * TILE_DEG
        while lng < e:
            tile = _tile("inland", code, lat, lng, lat + TILE_DEG, lng + TILE_DEG, country)
            out.append(Tile(tile.id, round(max(lat, s), 6), round(max(lng, w), 6),
                            round(min(lat + TILE_DEG, n), 6), round(min(lng + TILE_DEG, e), 6),
                            code, country, "inland"))
            lng += TILE_DEG
        lat += TILE_DEG
    return out


def tiles_for_state(code: str, bbox: tuple[float, float, float, float] | None = None) -> list[Tile]:
    """The tiles a state's pull walks: the hand-written coastal tiles that
    touch the state's box, or, for a state without any, tiles generated over
    its TIGER box (`bbox`, else the box table)."""
    code = code.upper()
    own = [t for t in TILES if t.state == code]
    if own:
        return [t for t in TILES if code in states_for_tile(t)]
    if bbox is None:
        if code not in STATE_BOXES:
            raise ValueError(f"unknown state: {code}")
        boxes = STATE_BOXES[code]
        bbox = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))
    return generated_tiles(code, bbox)


def dedupe(elements: list[dict]) -> list[dict]:
    """Drop repeats of the same OSM element (tiles overlap). First copy wins."""
    seen: set[tuple[str, int]] = set()
    out: list[dict] = []
    for el in elements:
        key = (el["type"], el["id"])
        if key not in seen:
            seen.add(key)
            out.append(el)
    return out
