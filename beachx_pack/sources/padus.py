"""USGS PAD-US public land at a point. Public domain.

Layer: "PAD-US Public Access" (USGS, ArcGIS Online), which carries Category,
Unit_Nm, Pub_Access, MngTp_Desc, MngNm_Desc, DesTp_Desc, GIS_Acres.
"""

from __future__ import annotations

from pathlib import Path

from ..http import request_json
from .common import load_points, per_point, targets

NAME = "padus"
BLOCK = "land"
SERVICE = ("https://services.arcgis.com/v01gqwM5QqNysAAi/arcgis/rest/services/"
           "PADUS_Public_Access/FeatureServer/0")
URL = SERVICE + "/query"
FIELDS = ("public_access", "land_manager_type", "land_manager_name", "designation",
          "padus_unit_name")
SOURCE = {"name": "USGS PAD-US (Protected Areas Database of the United States), "
                  "PAD-US Public Access layer", "url": SERVICE,
          "license": "Public domain (US government work)", "attribution": "USGS Gap Analysis Project, PAD-US"}
SLEEP = 0.3
SEARCH_M = 30  # metres around the lookup point
ACCESS = {"OA": "Open", "RA": "Restricted", "XA": "Closed", "UK": "Unknown"}
CATEGORY_RANK = {"Fee": 0, "Easement": 1, "Designation": 2, "Proclamation": 3, "Other": 4}


def fetch(rec: dict) -> tuple[str, dict]:
    lat, lon = rec["_point"]
    return request_json([URL], params={
        "geometry": f"{lon:.6f},{lat:.6f}", "geometryType": "esriGeometryPoint", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects", "distance": str(SEARCH_M),
        "units": "esriSRUnit_Meter", "returnGeometry": "false", "f": "json",
        "outFields": "Category,Unit_Nm,Pub_Access,MngTp_Desc,MngNm_Desc,DesTp_Desc,GIS_Acres",
    }, timeout=60, attempts=3, backoff=5)


def pull(raw_dir: Path, records: list[dict], opts: dict, log=print) -> dict:
    return per_point(NAME, raw_dir, targets(records), fetch, limit=opts.get("limit"),
                     force=opts.get("force", False), sleep=SLEEP, log=log)


def fields(response: dict) -> dict:
    """Pick one unit: Fee before Easement before Designation, then the smallest.

    The layer also has "Non-PAD-US Area" filler polygons (no Category); they
    are not protected areas and are ignored.
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


def fresh(raw_dir: Path, records: list[dict]) -> dict[str, dict] | None:
    points = load_points(raw_dir, NAME)
    if not points:
        return None
    return {rid: fields(p["response"]) for rid, p in points.items()}
