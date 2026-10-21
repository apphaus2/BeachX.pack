"""USGS 3DEP bare-earth elevation per grid cell. Public domain.

Beaches with a grid are grouped into clusters no wider or taller than
CLUSTER_M (greedy, nearest first). Each cluster gets one raster from the
3DEP dynamic image service (`exportImage`, Web Mercator, CELL_M ground
pixels, float32 GeoTIFF with LZ77) covering its beaches plus MARGIN_M, and
one catalog query for the source datasets under it (project name,
resolution, flight dates). The service times out (HTTP 504 after ten
minutes) on a full 4000-pixel export, so the cluster's raster is fetched as
pieces of at most PIECE_PX pixels a side, only those over a beach, each
with an OVERLAP_PX border so bilinear sampling never falls off a piece. Each beach's cell centres are sampled
bilinearly; the result goes to raw/<run-date>/terrain/<beach id>.json and the
raster is not kept.

Heights are in the raster's vertical datum: NAVD 88 in the conterminous US
and Alaska, local mean sea level where NAVD 88 does not exist (Hawaii,
Puerto Rico, the Virgin Islands). The 3DEP catalog labels Hawaii's lidar
NAVD 88; the hydro-flattened sea reads 0.0 there, which is local mean sea
level.
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from pathlib import Path

from .. import USER_AGENT, geotiff
from ..geometry import M_PER_DEG_LAT, M_PER_DEG_LNG
from ..grid import from_grid, to_grid
from ..http import FetchError, request_json
from .common import now, write_json

NAME = "terrain"
BLOCK = "terrain"
SERVICE = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer"
FIELDS = ("terrain_source", "terrain_project", "terrain_flight_start", "terrain_flight_end",
          "terrain_resolution_m", "terrain_datum", "terrain_lowest_m", "terrain_water_m")
SOURCE = {"name": "USGS 3D Elevation Program (3DEP) bare-earth DEM, dynamic image service",
          "url": SERVICE, "license": "Public domain (US government work)",
          "attribution": "U.S. Geological Survey, 3D Elevation Program"}
CLUSTER_M = 20_000.0
PIECE_PX = 1000
OVERLAP_PX = 2
CELL_M = 5.0
MARGIN_M = 30.0
WATER_PROBE_M = 15.0  # how far seaward of the water edge the water surface is read
R = 6378137.0
LOCAL_DATUM_STATES = {"HI", "PR", "VI", "GU", "AS", "MP"}
SLEEP = 1.0


def datum_for(state: str | None) -> str:
    return "LMSL" if state in LOCAL_DATUM_STATES else "NAVD88"


def merc(lat: float, lng: float) -> tuple[float, float]:
    return (R * math.radians(lng), R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


# ---------------------------------------------------------------- clusters

def beach_box(rec: dict) -> tuple[float, float, float, float] | None:
    b = rec["_shape"].get("boundary")
    if not b:
        return None
    pts = [p for r in b["rings"] for p in r]
    return (min(p[0] for p in pts), min(p[1] for p in pts),
            max(p[0] for p in pts), max(p[1] for p in pts))


def _size_m(box: tuple[float, float, float, float]) -> tuple[float, float]:
    s, w, n, e = box
    return ((e - w) * M_PER_DEG_LNG * math.cos(math.radians((s + n) / 2)),
            (n - s) * M_PER_DEG_LAT)


def clusters(items: list[tuple[str, tuple]]) -> list[list[tuple[str, tuple]]]:
    """Greedy: seed with the westernmost remaining beach, add the nearest
    others while the union box stays within CLUSTER_M on both sides."""
    left = sorted(items, key=lambda it: (it[1][1], it[1][0], it[0]))
    out = []
    while left:
        seed = left.pop(0)
        group, box = [seed], list(seed[1])
        cy, cx = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        rest = sorted(left, key=lambda it: ((it[1][0] - cy) ** 2
                                            + ((it[1][1] - cx) * math.cos(math.radians(cy))) ** 2,
                                            it[0]))
        keep = []
        for it in rest:
            union = (min(box[0], it[1][0]), min(box[1], it[1][1]),
                     max(box[2], it[1][2]), max(box[3], it[1][3]))
            wm, hm = _size_m(union)
            if wm <= CLUSTER_M and hm <= CLUSTER_M:
                group.append(it)
                box = list(union)
            else:
                keep.append(it)
        left = sorted(keep, key=lambda it: (it[1][1], it[1][0], it[0]))
        out.append(group)
    return out


# ---------------------------------------------------------------- fetch

def _get(url: str, params: dict, attempts: int = 5, backoff: float = 20, log=print) -> bytes:
    import urllib.parse
    full = url + "?" + urllib.parse.urlencode(params)
    wait, last = backoff, "no attempt"
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=300) as resp:
                body = resp.read()
                if "image" not in resp.headers.get("Content-Type", ""):
                    raise FetchError(f"not an image: {body[:200]!r}")
                return body
        except urllib.error.HTTPError as err:
            last = f"HTTP {err.code}"
            if err.code not in (429, 500, 502, 503, 504):
                raise FetchError(last) from err
        except (urllib.error.URLError, TimeoutError, ConnectionError, FetchError) as err:
            last = f"{type(err).__name__}: {err}"
        if attempt < attempts:
            log(f"    retry {attempt}/{attempts - 1} in {wait:.0f}s after {last}")
            time.sleep(wait)
            wait *= 2
    raise FetchError(last)


class Mosaic:
    """Pieces of one cluster raster; a point is read from the first piece
    that holds it at least one pixel inside its border."""

    def __init__(self, pieces: list[geotiff.Raster]):
        self.pieces = pieces
        self.width = sum(p.width for p in pieces)
        self.height = max((p.height for p in pieces), default=0)

    def sample(self, x: float, y: float) -> float:
        for p in self.pieces:
            if (p.x0 + p.sx <= x <= p.x0 + (p.width - 1) * p.sx
                    and p.y0 - (p.height - 1) * p.sy <= y <= p.y0 - p.sy):
                return p.sample(x, y)
        return math.nan


def _boxes_touch(a, b) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def fetch_raster(box: tuple[float, float, float, float], log=print,
                 beaches: list[tuple] | None = None) -> Mosaic:
    """The cluster raster over the box with CELL_M ground pixels, fetched as
    pieces of at most PIECE_PX a side, skipping pieces with no beach."""
    s, w, n, e = box
    dlat = MARGIN_M / M_PER_DEG_LAT
    dlng = MARGIN_M / (M_PER_DEG_LNG * math.cos(math.radians((s + n) / 2)))
    x0, y0 = merc(s - dlat, w - dlng)
    x1, y1 = merc(n + dlat, e + dlng)
    pixel = CELL_M / math.cos(math.radians((s + n) / 2))  # mercator metres per ground 5 m
    cols, rows = math.ceil((x1 - x0) / pixel), math.ceil((y1 - y0) / pixel)
    marks = [(*merc(b[0] - dlat, b[1] - dlng), *merc(b[2] + dlat, b[3] + dlng))
             for b in (beaches or [box])]  # (x0, y0, x1, y1) in mercator
    pieces = []
    for i in range(0, cols, PIECE_PX):
        for j in range(0, rows, PIECE_PX):
            c0, r0 = max(0, i - OVERLAP_PX), max(0, j - OVERLAP_PX)
            c1, r1 = min(cols, i + PIECE_PX + OVERLAP_PX), min(rows, j + PIECE_PX + OVERLAP_PX)
            px0, py0 = x0 + c0 * pixel, y0 + r0 * pixel
            px1, py1 = x0 + c1 * pixel, y0 + r1 * pixel
            if not any(m[0] <= px1 and px0 <= m[2] and m[1] <= py1 and py0 <= m[3] for m in marks):
                continue
            data = _get(SERVICE + "/exportImage", {
                "bbox": f"{px0:.3f},{py0:.3f},{px1:.3f},{py1:.3f}", "bboxSR": "3857",
                "imageSR": "3857", "size": f"{c1 - c0},{r1 - r0}", "format": "tiff",
                "pixelType": "F32", "noDataInterpretation": "esriNoDataMatchAny",
                "interpolation": "RSP_BilinearInterpolation", "compression": "LZ77",
                "f": "image"}, log=log)
            pieces.append(geotiff.read(data))
    return Mosaic(pieces)


def catalog(box: tuple[float, float, float, float], log=print) -> list[dict]:
    """Source datasets (primary catalog items) under the box, with outlines."""
    s, w, n, e = box
    _, data = request_json([SERVICE + "/query"], params={
        "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects", "where": "Category = 1",
        "outFields": "Name,LowPS,StartDate,EndDate,AcquisitionDate,VerticalDatum",
        "returnGeometry": "true", "outSR": "4326", "maxAllowableOffset": "0.0005",
        "f": "json"}, timeout=300, attempts=4, backoff=10, log=log)
    return [{"attrs": f.get("attributes", {}), "rings": (f.get("geometry") or {}).get("rings", [])}
            for f in data.get("features", [])]


def _ymd(value) -> str | None:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return None


def _inside(lat: float, lng: float, rings: list) -> bool:
    inside = False
    for ring in rings:
        for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
            if (y1 > lat) != (y2 > lat) and lng < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
    return inside


def source_at(items: list[dict], lat: float, lng: float) -> dict:
    """The finest dataset under a point, latest flight on a tie."""
    here = [it for it in items if it["rings"] and _inside(lat, lng, it["rings"])]
    if not here:
        return {}
    best = min(here, key=lambda it: (it["attrs"].get("LowPS") or 1e9,
                                     -(int(str(it["attrs"].get("EndDate") or 0)[:8] or 0)),
                                     it["attrs"].get("Name") or ""))
    a = best["attrs"]
    return {"terrain_project": a.get("Name"),
            "terrain_flight_start": _ymd(a.get("StartDate")),
            "terrain_flight_end": _ymd(a.get("EndDate")),
            "terrain_resolution_m": round((a.get("LowPS") or 0) * math.cos(math.radians(lat)), 1)
            or None}


# ---------------------------------------------------------------- sampling

def cell_centres(grid: dict) -> list[tuple[float, float]]:
    half = grid["cell_size_m"] / 2
    out = []
    for cy, spans in grid["rows"]:
        for x0, x1 in spans:
            for cx in range(x0, x1 + 1):
                out.append(from_grid(cx * grid["cell_size_m"] + half,
                                     cy * grid["cell_size_m"] + half, grid["anchor"]))
    return out


def sample(raster, lat: float, lng: float) -> float | None:
    v = raster.sample(*merc(lat, lng))
    return None if v != v else v


def water_level(raster, rec: dict) -> float | None:
    """Median raster value WATER_PROBE_M seaward (-y) of the water-edge
    vertices: the hydro-flattened water surface at the time of the flight."""
    b, g = rec["_shape"].get("boundary") or {}, rec["_shape"].get("grid")
    if not g or not b.get("water_edge"):
        return None
    vals = []
    for line in b["water_edge"]:
        for lat, lng in line:
            x, y = to_grid(lat, lng, g["anchor"])
            v = sample(raster, *from_grid(x, y - WATER_PROBE_M, g["anchor"]))
            if v is not None:
                vals.append(v)
    if not vals:
        return None
    vals.sort()
    return round(vals[len(vals) // 2], 2)


# ---------------------------------------------------------------- pull

def geometry_hash(rec: dict) -> str:
    return rec["_blocks"]["geometry"]["hash"]


def targets(records: list[dict], previous: dict, force: bool) -> list[dict]:
    """Beaches with a grid whose heights are missing or were taken for
    another shape."""
    out = []
    for r in records:
        if not r["_shape"].get("grid"):
            continue
        old = (previous.get(r["id"]) or {}).get("terrain")
        if force or not old or old["data"].get("geometry_hash") != geometry_hash(r):
            out.append(r)
    return out


def pull(raw_dir: Path, records: list[dict], opts: dict, log=print) -> dict:
    folder = raw_dir / NAME
    want = targets(records, opts.get("previous") or {}, opts.get("force", False))
    todo = [r for r in want if opts.get("force") or not (folder / f"{r['id']}.json").exists()]
    status = {"calls": 0, "ok": 0, "errors": 0, "cached": len(want) - len(todo),
              "eligible": len(want), "endpoint": SERVICE, "note": None}
    if opts.get("limit") is not None:
        todo = todo[:opts["limit"]]
    groups = clusters([(r["id"], beach_box(r)) for r in todo if beach_box(r)])
    by_id = {r["id"]: r for r in todo}
    log(f"  {NAME}: {len(todo)} beaches in {len(groups)} clusters "
        f"({status['cached']} already done)")
    started = time.time()
    streak = 0
    for k, group in enumerate(groups, 1):
        box = (min(b[0] for _, b in group), min(b[1] for _, b in group),
               max(b[2] for _, b in group), max(b[3] for _, b in group))
        wm, hm = _size_m(box)
        t0 = time.time()
        try:
            status["calls"] += 2
            raster = fetch_raster(box, log, [b for _, b in group])
            items = catalog(box, log)
        except FetchError as err:
            status["errors"] += 1
            streak += 1
            log(f"  {NAME}: cluster {k}/{len(groups)} failed: {err}")
            if streak >= 3:
                status["note"] = f"unreachable after {streak} failed clusters: {err}"
                break
            continue
        streak = 0
        status["ok"] += 1
        for rid, _ in group:
            rec = by_id[rid]
            grid = rec["_shape"]["grid"]
            heights = [sample(raster, lat, lng) for lat, lng in cell_centres(grid)]
            known = [h for h in heights if h is not None]
            info = source_at(items, *rec["_point"])
            write_json(folder / f"{rid}.json", {
                "beach_id": rid, "fetched_at": now(), "geometry_hash": geometry_hash(rec),
                "cluster": k, "raster": {"pieces": len(raster.pieces), "pixel_m": CELL_M},
                "fields": {"terrain_source": "USGS 3DEP", **{f: info.get(f) for f in (
                    "terrain_project", "terrain_flight_start", "terrain_flight_end",
                    "terrain_resolution_m")}, "terrain_datum": datum_for(rec.get("state")),
                    "terrain_lowest_m": round(min(known), 2) if known else None,
                    "terrain_water_m": water_level(raster, rec)},
                "heights_cm": [None if h is None else round(h * 100) for h in heights]})
        log(f"  {NAME}: cluster {k}/{len(groups)}: {len(group)} beaches, "
            f"{wm / 1000:.1f} x {hm / 1000:.1f} km, {len(raster.pieces)} pieces, "
            f"{time.time() - t0:.0f}s")
        time.sleep(SLEEP)
    status["seconds"] = round(time.time() - started, 1)
    return status


def load(raw_dir: Path) -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text())
            for p in sorted((raw_dir / NAME).glob("beach_*.json"))}


def fresh(raw_dir: Path, records: list[dict]) -> dict[str, dict] | None:
    """Fields for beaches pulled this run whose shape still matches. The
    heights themselves are read by assemble (heights_for)."""
    found = load(raw_dir)
    if not found:
        return None
    out = {}
    for r in records:
        got = found.get(r["id"])
        if got and got["geometry_hash"] == r["_blocks"]["geometry"]["hash"]:
            out[r["id"]] = got["fields"]
    return out
