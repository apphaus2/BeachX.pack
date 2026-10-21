"""Census Gazetteer places: the nearest named place to every beach. Public domain.

One national file (every incorporated place and census designated place,
with its internal point), downloaded once into raw/downloads/gazetteer/.
It names unnamed beaches: "Unnamed stretch near <place>".
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from .common import Nearest, download, downloads_dir, now, write_json

NAME = "gazetteer"
BLOCK = "places"
FILE = "2025_Gaz_place_national.zip"
URL = f"https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/{FILE}"
FIELDS = ("nearest_place", "nearest_place_km")
SOURCE = {"name": "US Census Gazetteer, places (2025)", "url": URL,
          "license": "Public domain (US government work)", "attribution": "US Census Bureau"}
# LSAD code -> the suffix Census appends to the place name
SUFFIX = {"57": " CDP", "25": " city", "43": " town", "47": " village", "21": " borough",
          "53": " borough", "55": " comunidad", "62": " zona urbana", "37": " municipality"}
MAX_KM = 30.0  # farther than this, a beach is not "near" the place


def short_name(name: str, lsad: str) -> str:
    suffix = SUFFIX.get(lsad)
    return name[:-len(suffix)] if suffix and name.endswith(suffix) else name


def parse(text: str) -> list[dict]:
    lines = text.splitlines()
    head = [h.strip() for h in lines[0].split("|")]
    col = {h: i for i, h in enumerate(head)}
    out = []
    for line in lines[1:]:
        cells = line.split("|")
        if len(cells) < len(head):
            continue
        out.append({"name": short_name(cells[col["NAME"]].strip(), cells[col["LSAD"]].strip()),
                    "geoid": cells[col["GEOID"]].strip(), "state": cells[col["USPS"]].strip(),
                    "lat": float(cells[col["INTPTLAT"]]), "lng": float(cells[col["INTPTLONG"]])})
    return out


def load_places(log=print) -> list[dict]:
    path = download(URL, downloads_dir() / NAME / FILE, log=log)
    with zipfile.ZipFile(path) as z:
        return parse(z.read(z.namelist()[0]).decode("utf-8", "replace"))


def nearest(places: list[dict], records: list[dict]) -> dict[str, dict]:
    index = Nearest([(p["lat"], p["lng"], {"name": p["name"], "geoid": p["geoid"]})
                     for p in places])
    out = {}
    for r in records:
        hit = index.find(*r["_point"])
        if hit and hit[1] <= MAX_KM:
            out[r["id"]] = {"nearest_place": hit[0]["name"], "nearest_place_km": round(hit[1], 1)}
        else:
            out[r["id"]] = dict.fromkeys(FIELDS)
    return out


def pull(raw_dir: Path, records: list[dict], opts: dict, log=print) -> dict:
    path = raw_dir / NAME / "nearest.json"
    if path.exists() and not opts.get("force"):
        log(f"  {NAME}: cached")
        return {"calls": 0, "ok": 0, "errors": 0, "cached": 1, "endpoint": URL, "note": None}
    places = load_places(log)
    found = nearest(places, records)
    write_json(path, {"fetched_at": now(), "file": FILE, "places": len(places), "beaches": found})
    log(f"  {NAME}: {len(places)} places, "
        f"{sum(1 for v in found.values() if v['nearest_place'])} of {len(found)} beaches near one")
    return {"calls": 1, "ok": 1, "errors": 0, "cached": 0, "endpoint": URL, "note": None}


def fresh(raw_dir: Path, records: list[dict]) -> dict[str, dict] | None:
    path = raw_dir / NAME / "nearest.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())["beaches"]
