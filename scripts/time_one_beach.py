"""Pull one beach through every part of the pipeline, live, and time each part.

    python3 scripts/time_one_beach.py way/218679862

Nothing is written to pack/. Raw responses are not cached.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beachx_pack import grid as gridlib  # noqa: E402
from beachx_pack import nearby, normalize  # noqa: E402
from beachx_pack.http import request_json  # noqa: E402
from beachx_pack.overpass import ENDPOINTS  # noqa: E402
from beachx_pack.sources import buoys, census, nws, padus, tides, timezone, wikipedia  # noqa: E402
from beachx_pack.sources.common import Nearest, haversine_km  # noqa: E402

rows: list[tuple[str, float, str]] = []


def timed(label: str, fn):
    start = time.time()
    try:
        out = fn()
        note = out if isinstance(out, str) else ""
    except Exception as error:  # noqa: BLE001
        out, note = None, f"failed: {type(error).__name__}: {error}"[:120]
    rows.append((label, time.time() - start, note))
    return out


def main(ref: str) -> None:
    osm_type, osm_id = ref.split("/")
    el_holder: dict = {}

    def pull_element():
        query = f"[out:json][timeout:120];{osm_type}({osm_id});out meta geom;"
        _, data = request_json(list(ENDPOINTS), data={"data": query}, timeout=180,
                               attempts=2, backoff=5, log=lambda *_: None)
        el_holder["el"] = data["elements"][0]
        return f"{len(el_holder['el'].get('geometry', []))} points, v{el_holder['el'].get('version')}"

    timed("Overpass: the element with geometry and meta", pull_element)
    el = el_holder["el"]
    tags = el.get("tags", {})
    bounds = el.get("bounds") or {}
    lat0 = (bounds["minlat"] + bounds["maxlat"]) / 2
    lng0 = (bounds["minlon"] + bounds["maxlon"]) / 2
    shape_holder: dict = {}

    def geometry():
        shape_holder["s"] = normalize.shape(el, lat0, lng0)
        s = shape_holder["s"]
        return f"area {s['area_m2']} m², {s['boundary']['point_count']} boundary points"

    timed("Geometry: rings, area, simplify", geometry)

    def grid():
        s = shape_holder["s"]
        g = gridlib.build_grid([[tuple(p) for p in r] for r in s["boundary"]["rings"]],
                               [[tuple(p) for p in r] for r in s["boundary"].get("holes", [])])
        return f"{g['cell_count']} cells, heading {g['anchor']['heading_degrees']}°"

    timed("Grid: heading, cells, run-length rows", grid)

    def nearby_pull():
        pad = 0.004
        box = (bounds["minlat"] - pad, bounds["minlon"] - pad, bounds["maxlat"] + pad, bounds["maxlon"] + pad)
        _, data = request_json(list(ENDPOINTS), data={"data": nearby.build_query(box)},
                               timeout=180, attempts=2, backoff=5, log=lambda *_: None)
        items = [i for i in (nearby.item_record(e) for e in data["elements"]) if i]
        kinds = {}
        for i in items:
            kinds[i["kind"]] = kinds.get(i["kind"], 0) + 1
        return f"{len(items)} items in the box: {kinds}"

    timed("Overpass: nearby webcams, shops, amenities", nearby_pull)

    def tide():
        _, data = request_json([tides.URL], timeout=120, log=lambda *_: None)
        pts = [(float(s["lat"]), float(s["lng"]), s) for s in data["stations"]]
        hit, km = Nearest(pts).find(lat0, lng0)
        return f"{len(pts)} stations downloaded, nearest {hit['name']} {km:.1f} km"

    timed("NOAA tide stations: download list, match nearest", tide)

    def buoy():
        _, data = request_json([buoys.URL], timeout=120, raw_text=True, log=lambda *_: None)
        kept = buoys.parse(data["text"])
        pts = [(b["lat"], b["lon"], b) for b in kept]
        hit, km = Nearest(pts).find(lat0, lng0)
        return f"{len(kept)} buoys downloaded, nearest {hit['id']} {km:.1f} km"

    timed("NOAA buoys: download table, match nearest", buoy)
    timed("Time zone: built-in table", lambda: timezone.zone("HI", lat0, lng0) or "none")

    rec = {"_point": normalize.lookup_point({"latitude": lat0, "longitude": lng0}, shape_holder["s"]["boundary"]),
           "wikipedia": tags.get("wikipedia")}
    timed("Census geocoder: county and place",
          lambda: json.dumps(census.fields(census.fetch(rec)[1])))
    def land():
        box = (lat0 - 0.1, lng0 - 0.1, lat0 + 0.1, lng0 + 0.1)
        units, source = padus.load_units("HI", box, log=lambda *_: None)
        hits = padus.Index(units).lookup(*rec["_point"])
        return f"{source}: " + json.dumps(padus.fields({"features": [{"attributes": a} for a in hits]}))

    timed("PAD-US: state download (cached), local lookup", land)
    timed("NWS: forecast zone and grid",
          lambda: json.dumps(nws.fields(nws.fetch(rec)[1])))

    qid = normalize.bare_qid(tags.get("wikidata"))

    def wikidata():
        if not qid:
            return "no wikidata tag"
        _, data = request_json(["https://www.wikidata.org/w/api.php"], params={
            "action": "wbgetentities", "ids": qid, "props": "labels|sitelinks|claims",
            "format": "json"}, timeout=60, log=lambda *_: None)
        ent = data["entities"][qid]
        return f"{qid}: {len(ent.get('sitelinks', {}))} sitelinks"

    timed("Wikidata: entity", wikidata)

    def wiki():
        if not rec["wikipedia"]:
            return "no wikipedia tag on this element"
        _, data = wikipedia.fetch(rec)
        page = data["query"]["pages"][0]
        return f"{len(page.get('extract', ''))} chars of extract"

    timed("Wikipedia: first paragraph", wiki)

    total = sum(t for _, t, _ in rows)
    print(f"\n{tags.get('name', ref)}  ({ref})\n")
    print(f"{'Part':<48}{'Seconds':>9}  Result")
    for label, secs, note in rows:
        print(f"{label:<48}{secs:>9.2f}  {note}")
    print(f"{'Total':<48}{total:>9.2f}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "way/218679862")
