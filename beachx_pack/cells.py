"""The 8-byte cell record and the rows blob (DECISIONS.md, 20 October 2026).

Record, little-endian, one per cell inside the boundary:
  height     int16   0.1 m above the beach's lowest cell; -32768 unknown
  water      int16   0.1 m from the cell centre to the nearest water-edge
                     segment, 0 when the cell lies against the water edge,
                     clamped at 32767 (also used when there is no water)
  volatility uint8   0 wash, 1 wet, 2 dry, 3 back, 255 unknown
  slope      uint8   whole degrees 0-90 to the steepest neighbour, 255 unknown
  flags      uint8   bit 0 build, bit 1 edge
  reserved   uint8   0
Rows blob: [y int16, x0 int16, count uint16, count x record] repeated, rows
in ascending y, runs in ascending x0.
"""

from __future__ import annotations

import base64
import math
import struct

VERSION = 1
RECORD = struct.Struct("<hhBBBB")
RUN = struct.Struct("<hhH")
HEIGHT_UNKNOWN = -32768
WATER_MAX = 32767
WASH, WET, DRY, BACK, UNKNOWN = 0, 1, 2, 3, 255
SLOPE_UNKNOWN = 255
BUILD, EDGE = 1, 2
BUILD_MAX_SLOPE = 10  # degrees
BACK_SLOPE = 20  # degrees: a dune face, wall, or cliff
SAND = {None, "sand"}  # beach surface tags that hold a castle (OSM assumes sand)


def runs_of(rows: list) -> list[tuple[int, int, int]]:
    """[(y, x0, count)] from [[y, [[x0, x1], ...]], ...] spans."""
    return [(cy, x0, x1 - x0 + 1) for cy, spans in rows for x0, x1 in spans]


def cells_of(rows: list) -> list[tuple[int, int]]:
    return [(x, y) for y, x0, n in runs_of(rows) for x in range(x0, x0 + n)]


def encode(runs: list[tuple[int, int, int]], records: list[tuple]) -> bytes:
    out = bytearray()
    k = 0
    for y, x0, n in runs:
        out += RUN.pack(y, x0, n)
        for rec in records[k:k + n]:
            out += RECORD.pack(*rec, 0) if len(rec) == 5 else RECORD.pack(*rec)
        k += n
    if k != len(records):
        raise ValueError(f"{len(records)} records for {k} cells")
    return bytes(out)


def decode(blob: bytes) -> tuple[list[tuple[int, int, int]], list[tuple]]:
    """(runs, records) with each record (height, water, volatility, slope, flags)."""
    runs, records = [], []
    pos = 0
    while pos < len(blob):
        y, x0, n = RUN.unpack_from(blob, pos)
        pos += RUN.size
        runs.append((y, x0, n))
        for _ in range(n):
            records.append(RECORD.unpack_from(blob, pos)[:5])
            pos += RECORD.size
    return runs, records


def b64(blob: bytes) -> str:
    return base64.b64encode(blob).decode("ascii")


def unb64(text: str) -> bytes:
    return base64.b64decode(text)


def spans_of(runs: list[tuple[int, int, int]]) -> list:
    rows: list = []
    for y, x0, n in runs:
        if rows and rows[-1][0] == y:
            rows[-1][1].append([x0, x0 + n - 1])
        else:
            rows.append([y, [[x0, x0 + n - 1]]])
    return rows


# ---------------------------------------------------------------- derivation

def slopes(cells: list[tuple[int, int]], heights_cm: list[int | None], size: float) -> list[int]:
    """Degrees to the steepest of the eight neighbours with a height."""
    at = {c: h for c, h in zip(cells, heights_cm) if h is not None}
    out = []
    for (x, y), h in zip(cells, heights_cm):
        if h is None:
            out.append(SLOPE_UNKNOWN)
            continue
        best = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == dy == 0:
                    continue
                other = at.get((x + dx, y + dy))
                if other is None:
                    continue
                run = size * math.hypot(dx, dy)
                angle = math.degrees(math.atan(abs(other - h) / 100.0 / run))
                best = angle if best is None else max(best, angle)
        out.append(SLOPE_UNKNOWN if best is None else min(90, round(best)))
    return out


def water_reference(rec: dict) -> tuple[float | None, float | None, str | None]:
    """(mean high water in the terrain's datum, metres above it that stay wet,
    what the reference is). The wet band is the highest astronomical tide
    plus the wave margin on the sea, the wave margin on a lake or river."""
    margin = rec.get("wave_margin_m") or 0.0
    kind = rec.get("water_kind")
    if kind in ("lake", "river"):
        level = rec.get("terrain_water_m")
        basis = "water surface at flight"
        if level is None:
            level, basis = rec.get("terrain_lowest_m"), "lowest cell"
        return level, margin, basis if level is not None else None
    hat = rec.get("tide_hat_above_mhw_m") or 0.0
    if rec.get("terrain_datum") == "NAVD88" and rec.get("tide_mhw_above_navd88_m") is not None:
        return rec["tide_mhw_above_navd88_m"], hat + margin, "MHW over NAVD88"
    if rec.get("tide_mhw_above_msl_m") is not None:
        if rec.get("terrain_sea_level_m") is not None:  # the lidar's flattened sea
            return (rec["terrain_sea_level_m"] + rec["tide_mhw_above_msl_m"], hat + margin,
                    "MHW over the flattened sea")
        return rec["tide_mhw_above_msl_m"], hat + margin, "MHW over MSL"
    return None, None, None


def records(grid: dict, rec: dict, heights_cm: list[int | None] | None) -> tuple[list[tuple], dict]:
    """Every cell's record, and the per-beach numbers the phone needs:
    datum_offset_m (add to a cell height in metres to get metres above mean
    high water) and wet_limit_m (metres above mean high water that are wet)."""
    cells = cells_of(grid["rows"])
    n = len(cells)
    heights_cm = heights_cm if heights_cm and len(heights_cm) == n else [None] * n
    known = [h for h in heights_cm if h is not None]
    low = min(known) if known else None
    rel = [HEIGHT_UNKNOWN if h is None else round((h - low) / 10) for h in heights_cm]
    slope = slopes(cells, heights_cm, grid["cell_size_m"])
    mhw, wet, basis = water_reference(rec)
    offset = round(low / 100 - mhw, 2) if low is not None and mhw is not None else None
    vol = []
    for r in rel:
        if r == HEIGHT_UNKNOWN or offset is None:
            vol.append(UNKNOWN)
            continue
        above = r / 10 + offset
        vol.append(WASH if above < 0 else WET if above <= wet + 1e-9 else DRY)
    # back: a dry cell that rises at least BACK_SLOPE from the cell on the
    # water side of it (y - 1), and every dry cell above it in the same column
    at = {c: h for c, h in zip(cells, heights_cm)}
    rise = math.tan(math.radians(BACK_SLOPE)) * grid["cell_size_m"] * 100
    columns: dict[int, list[int]] = {}
    for i, (x, y) in enumerate(cells):
        columns.setdefault(x, []).append(i)
    for idx in columns.values():
        behind = False
        for i in sorted(idx, key=lambda i: cells[i][1]):
            if vol[i] != DRY:
                continue
            x, y = cells[i]
            below = at.get((x, y - 1))
            if behind or (below is not None and heights_cm[i] - below >= rise):
                vol[i] = BACK
                behind = True
    water = [WATER_MAX if w is None else min(WATER_MAX, w) for w in grid["water_dm"]]
    sand = rec.get("surface") in SAND
    out = []
    for i in range(n):
        edge = bool(grid["edge"][i])
        build = (vol[i] in (WET, DRY) and sand and slope[i] != SLOPE_UNKNOWN
                 and slope[i] <= BUILD_MAX_SLOPE and not edge)
        out.append((rel[i], water[i], vol[i], slope[i], (BUILD if build else 0) | (EDGE if edge else 0)))
    return out, {"datum_offset_m": offset, "wet_limit_m": round(wet, 2) if wet is not None else None,
                 "tide_reference": basis}


def pack_grid(grid: dict, recs: list[tuple]) -> dict:
    """The grid as written to the pack: frame, bounds, and the rows blob."""
    return {"anchor": grid["anchor"], "cell_size_m": grid["cell_size_m"],
            "cell_count": grid["cell_count"], "x_min": grid["x_min"], "x_max": grid["x_max"],
            "y_min": grid["y_min"], "y_max": grid["y_max"], "cell_record_version": VERSION,
            "rows": b64(encode(runs_of(grid["rows"]), recs))}


def unpack_grid(stored: dict) -> tuple[dict, list[tuple]]:
    """(shape grid with spans, water_dm, and edge; records) from a pack grid."""
    runs, recs = decode(unb64(stored["rows"]))
    grid = {k: stored[k] for k in ("anchor", "cell_size_m", "cell_count", "x_min", "x_max",
                                   "y_min", "y_max")}
    grid["rows"] = spans_of(runs)
    grid["water_dm"] = [r[1] for r in recs]
    grid["edge"] = [bool(r[4] & EDGE) for r in recs]
    return grid, recs
