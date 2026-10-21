"""USGS PAD-US public land, downloaded whole per state and looked up locally.
Public domain.

The official download is the ScienceBase "PAD-US 4.1 State Downloads" item:
one zip per state holding a file geodatabase and KMZ layers. No GeoPackage
is published per state, so the Combined layer's KMZ (Fee, Easement,
Designation, Proclamation; attributes in each placemark's HTML table,
coordinates in WGS 84) is read with zipfile and ElementTree. When the
download fails, a paged bulk export of the "PAD-US Public Access" feature
service over the state's box is the fallback. Either way the file is kept in
raw/downloads/padus/ and every beach, any tier, is looked up locally:
the units within SEARCH_M of the beach's lookup point.
"""

from __future__ import annotations

import io
import json
import math
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from ..geometry import M_PER_DEG_LAT, M_PER_DEG_LNG
from ..http import FetchError, request_json
from .common import download, downloads_dir, missing, now, write_json

NAME = "padus"
BLOCK = "land"
ITEM = "6759abcfd34edfeb8710a004"  # ScienceBase: PAD-US 4.1 State Downloads
STATE_FILE = "PADUS4_1_State_{state}_GDB_KMZ.zip"
DOWNLOAD_URL = "https://www.sciencebase.gov/catalog/file/get/" + ITEM + "?name={name}"
SERVICE = ("https://services.arcgis.com/v01gqwM5QqNysAAi/arcgis/rest/services/"
           "PADUS_Public_Access/FeatureServer/0")
FIELDS = ("public_access", "land_manager_type", "land_manager_name", "designation",
          "padus_unit_name")
SOURCE = {"name": "USGS PAD-US 4.1 (Protected Areas Database of the United States), "
                  "state download, Combined layer",
          "url": f"https://www.sciencebase.gov/catalog/item/{ITEM}",
          "fallback": SERVICE,
          "license": "Public domain (US government work)",
          "attribution": "USGS Gap Analysis Project, PAD-US"}
SEARCH_M = 30  # metres around the lookup point
PAGE = 2000
ACCESS = {"OA": "Open", "RA": "Restricted", "XA": "Closed", "UK": "Unknown"}
CATEGORY_RANK = {"Fee": 0, "Easement": 1, "Designation": 2, "Proclamation": 3, "Other": 4}
# Code columns and the geodatabase coded-value domain that spells them out.
# The KML's d_* columns are often blank, so the codes are decoded from the
# domains stored (as XML) in the state geodatabase's item table.
DOMAINS = {"Mang_Type": "Agency Type", "Mang_Name": "Agency Name", "Des_Tp": "Designation Type"}
DOMAIN_XML = re.compile(r"<GPCodedValueDomain2.*?</GPCodedValueDomain2>", re.S)
KML = "{http://www.opengis.net/kml/2.2}"
CELL = re.compile(r"<td>([^<]*)</td>\s*<td>([^<]*)</td>")


# ---------------------------------------------------------------- reading

def _ring(text: str) -> list[tuple[float, float]]:
    pts = []
    for part in text.split():
        bits = part.split(",")
        if len(bits) >= 2:
            pts.append((float(bits[1]), float(bits[0])))
    return pts


def _unit(attrs: dict, polygons: list) -> dict | None:
    """One feature in the shape the lookup uses."""
    rings = [r for poly in polygons for r in poly if len(r) >= 4]
    if not rings:
        return None
    lats = [p[0] for r in rings for p in r]
    lngs = [p[1] for r in rings for p in r]
    return {"attrs": attrs, "polygons": polygons,
            "bbox": (min(lats), min(lngs), max(lats), max(lngs))}


def read_domains(text: str) -> dict[str, dict[str, str]]:
    """{domain name: {code: description}} from geodatabase item XML."""
    out = {}
    for block in DOMAIN_XML.findall(text):
        name = re.search(r"<DomainName>(.*?)</DomainName>", block)
        if name:
            out[name.group(1)] = {code: label for label, code in re.findall(
                r"<Name>(.*?)</Name><Code[^>]*>(.*?)</Code>", block)}
    return out


def _decoded(raw: dict, column: str, described: str, domains: dict) -> str | None:
    if raw.get(described):
        return raw[described]
    code = raw.get(column) or ""
    return domains.get(DOMAINS[column], {}).get(code) or code or None


def read_kml(data: bytes, domains: dict | None = None) -> list[dict]:
    """Placemarks of a PAD-US KML: attributes from the HTML table, polygons as
    [[outer, hole, ...], ...] with (lat, lng) points."""
    domains = domains or {}
    units = []
    for _, el in ElementTree.iterparse(io.BytesIO(data), events=("end",)):
        if el.tag != KML + "Placemark":
            continue
        desc = el.findtext(KML + "description") or ""
        raw = {k.strip(): v.strip() for k, v in CELL.findall(desc)}
        attrs = {
            "Category": raw.get("Category"),
            "Unit_Nm": raw.get("Unit_Nm"),
            "Pub_Access": raw.get("Pub_Access"),
            "MngTp_Desc": _decoded(raw, "Mang_Type", "d_Mang_Typ", domains),
            "MngNm_Desc": _decoded(raw, "Mang_Name", "d_Mang_Nam", domains),
            "DesTp_Desc": _decoded(raw, "Des_Tp", "d_Des_Tp", domains),
            "GIS_Acres": float(raw["GIS_Acres"]) if raw.get("GIS_Acres") else None,
        }
        polygons = []
        for poly in el.iter(KML + "Polygon"):
            rings = []
            for tag in ("outerBoundaryIs", "innerBoundaryIs"):
                for b in poly.findall(KML + tag):
                    coords = b.findtext(f"{KML}LinearRing/{KML}coordinates")
                    if coords:
                        rings.append(_ring(coords))
            if rings:
                polygons.append(rings)
        unit = _unit(attrs, polygons)
        if unit:
            units.append(unit)
        el.clear()
    return units


def read_state_zip(path: Path) -> list[dict]:
    with zipfile.ZipFile(path) as outer:
        names = [n for n in outer.namelist() if "Combined" in n and n.endswith(".kmz")]
        if not names:
            raise FetchError(f"{path.name} has no Combined KMZ")
        domains: dict = {}
        for name in outer.namelist():
            if name.endswith(".gdbtable") and outer.getinfo(name).file_size < 50_000_000:
                text = outer.read(name).decode("utf-8", "replace")
                if "GPCodedValueDomain2" in text:
                    domains.update(read_domains(text))
        with zipfile.ZipFile(io.BytesIO(outer.read(names[0]))) as kmz:
            kml = [n for n in kmz.namelist() if n.endswith(".kml")][0]
            return read_kml(kmz.read(kml), domains)


def read_geojson(data: dict) -> list[dict]:
    units = []
    for feat in data.get("features", []):
        geom = feat.get("geometry") or {}
        if geom.get("type") == "Polygon":
            polys = [geom["coordinates"]]
        elif geom.get("type") == "MultiPolygon":
            polys = geom["coordinates"]
        else:
            continue
        polygons = [[[(p[1], p[0]) for p in ring] for ring in poly] for poly in polys]
        props = feat.get("properties") or {}
        unit = _unit({k: props.get(k) for k in ("Category", "Unit_Nm", "Pub_Access",
                                                 "MngTp_Desc", "MngNm_Desc", "DesTp_Desc",
                                                 "GIS_Acres")}, polygons)
        if unit:
            units.append(unit)
    return units


# ---------------------------------------------------------------- lookup

def _inside(lat: float, lng: float, ring: list) -> bool:
    inside = False
    for (y1, x1), (y2, x2) in zip(ring, ring[1:]):
        if (y1 > lat) != (y2 > lat) and lng < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _near(lat: float, lng: float, ring: list, metres: float) -> bool:
    kx = M_PER_DEG_LNG * math.cos(math.radians(lat))
    pts = [((b - lng) * kx, (a - lat) * M_PER_DEG_LAT) for a, b in ring]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        dx, dy = x2 - x1, y2 - y1
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / (dx * dx + dy * dy)))
        if math.hypot(x1 + t * dx, y1 + t * dy) <= metres:
            return True
    return False


class Index:
    """Units bucketed by 0.05 degree cells of their box."""

    STEP = 0.05

    def __init__(self, units: list[dict]):
        self.units = units
        self.cells: dict[tuple[int, int], list[int]] = {}
        for i, u in enumerate(units):
            s, w, n, e = u["bbox"]
            for a in range(math.floor(s / self.STEP), math.floor(n / self.STEP) + 1):
                for b in range(math.floor(w / self.STEP), math.floor(e / self.STEP) + 1):
                    self.cells.setdefault((a, b), []).append(i)

    def lookup(self, lat: float, lng: float, metres: float = SEARCH_M) -> list[dict]:
        dlat = metres / M_PER_DEG_LAT
        dlng = metres / (M_PER_DEG_LNG * math.cos(math.radians(lat)))
        found = []
        for i in sorted(set(self.cells.get((math.floor(lat / self.STEP),
                                             math.floor(lng / self.STEP)), []))):
            u = self.units[i]
            s, w, n, e = u["bbox"]
            if not (s - dlat <= lat <= n + dlat and w - dlng <= lng <= e + dlng):
                continue
            for poly in u["polygons"]:
                if (_inside(lat, lng, poly[0]) and not any(_inside(lat, lng, h) for h in poly[1:])) \
                        or any(_near(lat, lng, r, metres) for r in poly):
                    found.append(u["attrs"])
                    break
        return found


def fields(response: dict) -> dict:
    """Pick one unit: Fee before Easement before Designation, then the smallest.

    `response` is {"features": [{"attributes": {...}}]}, the shape the old
    per-point service answered with. "Non-PAD-US Area" filler is ignored.
    """
    feats = [f.get("attributes", {}) for f in response.get("features", [])]
    feats = [a for a in feats if a.get("Category") in CATEGORY_RANK
             and a.get("Unit_Nm") != "Non-PAD-US Area"]
    if not feats:
        return dict.fromkeys(FIELDS)
    best = min(feats, key=lambda a: (CATEGORY_RANK.get(a.get("Category"), 9),
                                     a.get("GIS_Acres") or 0, a.get("Unit_Nm") or ""))
    return {
        "public_access": ACCESS.get(best.get("Pub_Access"), best.get("Pub_Access")),
        "land_manager_type": best.get("MngTp_Desc"),
        "land_manager_name": best.get("MngNm_Desc"),
        "designation": best.get("DesTp_Desc"),
        "padus_unit_name": best.get("Unit_Nm"),
    }


# ---------------------------------------------------------------- pull

def _bulk_export(state: str, box: tuple[float, float, float, float], log) -> Path:
    """Fallback: every unit intersecting the state's box, paged, as GeoJSON."""
    path = downloads_dir() / NAME / f"{state}_export.geojson"
    if path.exists():
        return path
    s, w, n, e = box
    features, offset = [], 0
    while True:
        _, page = request_json([SERVICE + "/query"], params={
            "where": "1=1", "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope",
            "inSR": "4326", "spatialRel": "esriSpatialRelIntersects", "outSR": "4326",
            "outFields": "Category,Unit_Nm,Pub_Access,MngTp_Desc,MngNm_Desc,DesTp_Desc,GIS_Acres",
            "returnGeometry": "true", "resultOffset": str(offset), "resultRecordCount": str(PAGE),
            "orderByFields": "OBJECTID", "f": "geojson"}, timeout=300, attempts=4, backoff=10,
            log=log)
        got = page.get("features", [])
        features += got
        log(f"  {NAME}: export page at {offset}: {len(got)} units")
        if len(got) < PAGE and not page.get("exceededTransferLimit") \
                and not (page.get("properties") or {}).get("exceededTransferLimit"):
            break
        offset += len(got)
        if not got:
            break
    write_json(path, {"type": "FeatureCollection", "features": features})
    return path


def load_units(state: str, box, log=print) -> tuple[list[dict], str]:
    """(units, where they came from) for one state, downloading once."""
    name = STATE_FILE.format(state=state)
    path = downloads_dir() / NAME / name
    try:
        download(DOWNLOAD_URL.format(name=name), path, log=log)
        return read_state_zip(path), name
    except (FetchError, zipfile.BadZipFile, OSError) as err:
        log(f"  {NAME}: state download failed ({err}); using the paged bulk export")
    path = _bulk_export(state, box, log)
    return read_geojson(json.loads(path.read_text())), path.name


def pull(raw_dir: Path, records: list[dict], opts: dict, log=print) -> dict:
    status = {"calls": 0, "ok": 0, "errors": 0, "cached": 0, "eligible": len(records),
              "endpoint": None, "note": None}
    out_path = raw_dir / NAME / "lookup.json"
    if out_path.exists() and not opts.get("force") and not missing(out_path, records):
        status["cached"] = 1
        log(f"  {NAME}: cached")
        return status
    by_state: dict[str, list[dict]] = {}
    for r in records:
        if r.get("state"):
            by_state.setdefault(r["state"], []).append(r)
    beaches: dict[str, dict] = {}
    files = {}
    for state, recs in sorted(by_state.items()):
        lats = [r["_point"][0] for r in recs]
        lngs = [r["_point"][1] for r in recs]
        box = (min(lats) - 0.1, min(lngs) - 0.1, max(lats) + 0.1, max(lngs) + 0.1)
        status["calls"] += 1
        units, source = load_units(state, box, log)
        files[state] = {"file": source, "units": len(units)}
        index = Index(units)
        for r in recs:
            hits = index.lookup(*r["_point"])
            beaches[r["id"]] = fields({"features": [{"attributes": a} for a in hits]})
        status["ok"] += 1
        log(f"  {NAME}: {state}: {len(units)} units from {source}, "
            f"{sum(1 for r in recs if beaches[r['id']]['padus_unit_name'])} of {len(recs)} "
            f"beaches on PAD-US land")
    status["endpoint"] = SOURCE["url"]
    write_json(out_path, {"fetched_at": now(), "files": files, "search_m": SEARCH_M,
                          "beaches": beaches})
    return status


def fresh(raw_dir: Path, records: list[dict]) -> dict[str, dict] | None:
    path = raw_dir / NAME / "lookup.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())["beaches"]
