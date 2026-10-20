"""US Census Geocoder: county and place for a point. Public domain."""

from __future__ import annotations

from pathlib import Path

from ..http import request_json
from .common import load_points, per_point, targets

NAME = "census"
BLOCK = "places"
URL = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
FIELDS = ("county", "county_fips", "place", "place_fips", "census_state")
SOURCE = {"name": "US Census Geocoder (geographies by coordinates)", "url": URL,
          "license": "Public domain (US government work)", "attribution": "US Census Bureau"}
SLEEP = 0.5


def fetch(rec: dict) -> tuple[str, dict]:
    lat, lon = rec["_point"]
    return request_json([URL], params={
        "x": f"{lon:.6f}", "y": f"{lat:.6f}", "benchmark": "Public_AR_Current",
        "vintage": "Current_Current", "format": "json"}, timeout=60, attempts=3, backoff=5)


def pull(raw_dir: Path, records: list[dict], opts: dict, log=print) -> dict:
    return per_point(NAME, raw_dir, targets(records), fetch, limit=opts.get("limit"),
                     force=opts.get("force", False), sleep=SLEEP, log=log)


def fields(response: dict) -> dict:
    geo = (response.get("result") or {}).get("geographies") or {}
    county = (geo.get("Counties") or [None])[0]
    place = (geo.get("Incorporated Places") or geo.get("Census Designated Places") or [None])[0]
    state = (geo.get("States") or [None])[0]
    return {
        "county": county.get("NAME") if county else None,
        "county_fips": county.get("GEOID") if county else None,
        "place": place.get("NAME") if place else None,
        "place_fips": place.get("GEOID") if place else None,
        "census_state": state.get("STUSAB") if state else None,
    }


def fresh(raw_dir: Path, records: list[dict]) -> dict[str, dict] | None:
    points = load_points(raw_dir, NAME)
    if not points:
        return None
    return {rid: fields(p["response"]) for rid, p in points.items()}
