"""NWS api.weather.gov points: forecast zone, county zone, office, grid. Public domain."""

from __future__ import annotations

from pathlib import Path

from ..http import request_json
from .common import load_points, per_point, targets

NAME = "nws"
BLOCK = "weather"
URL = "https://api.weather.gov/points/"
FIELDS = ("nws_forecast_zone", "nws_county_zone", "nws_office", "nws_grid")
SOURCE = {"name": "National Weather Service API (points)", "url": "https://api.weather.gov",
          "license": "Public domain (US government work)", "attribution": "NOAA National Weather Service"}
SLEEP = 0.5


def fetch(rec: dict) -> tuple[str, dict]:
    lat, lon = rec["_point"]
    return request_json([f"{URL}{lat:.4f},{lon:.4f}"], timeout=60, attempts=3, backoff=5,
                        accept_status=frozenset({404}),
                        headers={"Accept": "application/geo+json"})


def pull(raw_dir: Path, records: list[dict], opts: dict, log=print) -> dict:
    return per_point(NAME, raw_dir, targets(records), fetch, limit=opts.get("limit"),
                     force=opts.get("force", False), sleep=SLEEP, log=log)


def _last(url: str | None) -> str | None:
    return url.rstrip("/").rsplit("/", 1)[-1] if url else None


def fields(response: dict) -> dict:
    p = response.get("properties") or {}
    if not p:
        return dict.fromkeys(FIELDS)
    grid = ({"gridId": p.get("gridId"), "gridX": p.get("gridX"), "gridY": p.get("gridY")}
            if p.get("gridId") else None)
    return {"nws_forecast_zone": _last(p.get("forecastZone")),
            "nws_county_zone": _last(p.get("county")),
            "nws_office": p.get("cwa"), "nws_grid": grid}


def fresh(raw_dir: Path, records: list[dict]) -> dict[str, dict] | None:
    points = load_points(raw_dir, NAME)
    if not points:
        return None
    return {rid: fields(p["response"]) for rid, p in points.items()}
