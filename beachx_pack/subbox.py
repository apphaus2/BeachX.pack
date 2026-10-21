"""Nearby and water queries asked per sub-box of a tile, by bounding box.

A tile's nearby and water answers both start from the tile's beaches and
reached out with `around`. On the first mainland tile (San Diego: 342
beaches, 138 of them multipolygon relations) that broke on every server:
overpass-api.de's `around` dropped ways (the canary found 37 of 342 beaches
within 1 m of themselves, the same answer every time, and 4 of 47 on a
small box), and the whole-tile query ran past the gateway timeout on
overpass.kumi.systems and overpass.private.coffee; a quarter-degree part
still took up to 17 minutes there.

So `around` is not asked any more. The tile is cut into SPLIT_DEG cells and
only the cells that a beach's bounds touch are asked (from the tile's beach
answer on disk). In each cell's query every `<filter>(around.b:R)` line of
the kind's own query becomes the same filter over the boxes of that cell's
beaches widened by R metres (overlapping boxes merged): an index lookup, and
a superset of what `around` returned, which the build cuts back by exact
distance (nearby: 300 m to the outline; water: features whose box comes
within 500 m of the beach's box). The canary becomes a count check: the
cell's beaches as the server counts them must be at least CANARY_SHARE of
the beaches on disk whose centre lies in the cell. Each cell's answer is
kept in raw/<st>/<run-date>/<kind>/_parts/<tile>/ as it arrives, and the
tile file is the merge: elements deduplicated by type and id, counts summed.
"""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

from .http import FetchError, request_json

SPLIT_DEG = 0.5
SLEEP_BETWEEN_PARTS = 2.0
CANARY_SHARE = 0.9
MERGE_GROWTH = 1.5  # merge two widened boxes when their union is at most this much larger
M_PER_DEG_LAT = 110540.0
M_PER_DEG_LNG = 111320.0
AROUND = re.compile(r"^(\s*)((?:node|way|relation|nwr|nw|wr|nr)(?:\[[^\]]*\])+)"
                    r"\(around\.b:([0-9.]+)\);\s*$")
CANARY = 'nwr["natural"="beach"](around.b:1);'

Box = tuple[float, float, float, float]


def _bounds(el: dict) -> Box | None:
    if el.get("type") == "node" and "lat" in el:
        return (el["lat"], el["lon"], el["lat"], el["lon"])
    b = el.get("bounds")
    if b:
        return (b["minlat"], b["minlon"], b["maxlat"], b["maxlon"])
    c = el.get("center")
    if c:
        return (c["lat"], c["lon"], c["lat"], c["lon"])
    return None


def _beaches(elements: list[dict]) -> list[Box]:
    return [b for el in elements if el.get("type") in ("node", "way", "relation")
            for b in [_bounds(el)] if b]


class Cells:
    """The step-degree cells of a tile, rows south to north, columns west to east."""

    def __init__(self, bbox: Box, step: float = SPLIT_DEG):
        self.s, self.w, self.n, self.e = bbox
        self.step = step
        self.rows = max(1, math.ceil((self.n - self.s) / step - 1e-9))
        self.cols = max(1, math.ceil((self.e - self.w) / step - 1e-9))

    def row(self, lat: float) -> int:
        return min(self.rows - 1, max(0, math.floor((lat - self.s) / self.step)))

    def col(self, lng: float) -> int:
        return min(self.cols - 1, max(0, math.floor((lng - self.w) / self.step)))

    def box(self, i: int, j: int) -> Box:
        return (round(self.s + i * self.step, 6), round(self.w + j * self.step, 6),
                round(min(self.n, self.s + (i + 1) * self.step), 6),
                round(min(self.e, self.w + (j + 1) * self.step), 6))

    def touched(self, b: Box) -> list[tuple[int, int]]:
        """Cells the box meets inside the tile; [] when it is outside."""
        lo_s, lo_w, hi_n, hi_e = max(self.s, b[0]), max(self.w, b[1]), min(self.n, b[2]), min(self.e, b[3])
        if lo_s > hi_n or lo_w > hi_e:
            return []
        return [(i, j) for i in range(self.row(lo_s), self.row(hi_n) + 1)
                for j in range(self.col(lo_w), self.col(hi_e) + 1)]


def parts(bbox: Box, beach_elements: list[dict], step: float = SPLIT_DEG
          ) -> list[tuple[Box, list[Box], int]]:
    """(cell box, bounds of the beaches that touch it, beaches whose centre
    is in it) for every cell a beach touches. Every beach that meets the
    tile is in at least one cell."""
    grid = Cells(bbox, step)
    touching: dict[tuple[int, int], list[Box]] = {}
    centred: dict[tuple[int, int], int] = {}
    for b in _beaches(beach_elements):
        cells = grid.touched(b)
        for c in cells:
            touching.setdefault(c, []).append(b)
        if cells:
            c = (grid.row((b[0] + b[2]) / 2), grid.col((b[1] + b[3]) / 2))
            centred[c] = centred.get(c, 0) + 1
    return [(grid.box(*c), touching[c], centred.get(c, 0)) for c in sorted(touching)]


def widen(boxes: list[Box], metres: float) -> list[Box]:
    """Each box widened by `metres` on every side, overlapping boxes merged
    while the union stays within MERGE_GROWTH of their summed areas, in a
    stable order."""
    def area(b: Box) -> float:
        return (b[2] - b[0]) * (b[3] - b[1]) * math.cos(math.radians((b[0] + b[2]) / 2))

    out: list[Box] = []
    for s, w, n, e in sorted(boxes):
        dlat = metres / M_PER_DEG_LAT
        dlng = metres / (M_PER_DEG_LNG * max(math.cos(math.radians((s + n) / 2)), 0.01))
        out.append((s - dlat, w - dlng, n + dlat, e + dlng))
    merged = True
    while merged:
        merged = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                a, b = out[i], out[j]
                if a[0] > b[2] or b[0] > a[2] or a[1] > b[3] or b[1] > a[3]:
                    continue
                u = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                if area(u) <= MERGE_GROWTH * (area(a) + area(b)):
                    out[i] = u
                    del out[j]
                    merged = True
                    break
            if merged:
                break
    return [tuple(round(v, 6) for v in b) for b in out]


def bbox_query(query: str, beach_boxes: list[Box]) -> str:
    """The kind's own query with every `<filter>(around.b:R)` line turned into
    the same filter over the beach boxes widened by R metres, and the
    `around` canary turned into a second count of the beaches."""
    out = []
    for line in query.splitlines():
        if line.strip() == CANARY:
            out.append(".b;")
            continue
        m = AROUND.match(line)
        if m:
            pad, statement, radius = m.groups()
            out += [f"{pad}{statement}({b[0]:.6f},{b[1]:.6f},{b[2]:.6f},{b[3]:.6f});"
                    for b in widen(beach_boxes, float(radius))]
            continue
        if "around" in line:
            raise ValueError(f"cannot ask this line by box: {line.strip()}")
        out.append(line)
    return "\n".join(out) + "\n"


def count_check(expected: int):
    """validate(): the server saw at least CANARY_SHARE of the beaches the
    tile's beach answer has in this cell (a silently partial answer sees fewer)."""
    def check(response: dict) -> None:
        counts = [e for e in response.get("elements", []) if e.get("type") == "count"]
        if len(counts) != 2:
            raise FetchError("box answer is missing its beach counts")
        seen = int(counts[0]["tags"]["total"])
        if seen < CANARY_SHARE * expected:
            raise FetchError(f"server saw {seen} of {expected} beaches in the cell; retrying")
    return check


def merge(responses: list[dict]) -> dict:
    """One response from the parts: elements deduplicated by type and id in
    first-seen order, then the two counts summed, under the oldest part's
    `osm3s` (so the tile shows the oldest database it was read from)."""
    seen: set[tuple[str, int]] = set()
    elements: list[dict] = []
    counts: list[dict[str, int]] = [{}, {}]
    for resp in responses:
        mine = [el for el in resp.get("elements", []) if el.get("type") == "count"]
        for k, c in enumerate(mine[:2]):
            for key, v in c.get("tags", {}).items():
                counts[k][key] = counts[k].get(key, 0) + int(v)
        for el in resp.get("elements", []):
            if el.get("type") == "count":
                continue
            key = (el.get("type"), el.get("id"))
            if key in seen:
                continue
            seen.add(key)
            elements.append(el)
    keys = ("nodes", "ways", "relations", "total")
    elements += [{"type": "count", "id": 0,
                  "tags": {k: str(c.get(k, 0)) for k in keys}} for c in counts]
    stamps = [r["osm3s"] for r in responses if (r.get("osm3s") or {}).get("timestamp_osm_base")]
    out = {"elements": elements}
    if stamps:
        out = {"osm3s": min(stamps, key=lambda o: o["timestamp_osm_base"]), **out}
    return out


def part_path(raw_dir: Path, kind: str, tile_id: str, box: Box) -> Path:
    return raw_dir / kind / "_parts" / tile_id / f"{box[0]:g}_{box[1]:g}.json"


def fetch(tile, raw_dir: Path, endpoints: list[str], kind: str, query_for, log=print
          ) -> tuple[str | None, dict, int]:
    """(last endpoint, merged response, number of parts) for one tile, each
    part fetched once and kept, so a killed pull resumes from the last part.
    `query_for(bbox)` is the kind's own `around` query for a box."""
    beach_file = raw_dir / "osm" / f"{tile.id}.json"
    beaches = json.loads(beach_file.read_text())["response"].get("elements", [])
    todo = parts(tile.bbox, beaches)
    responses, url = [], None
    fetched = 0
    for k, (box, boxes, expected) in enumerate(todo, 1):
        path = part_path(raw_dir, kind, tile.id, box)
        if path.exists():
            payload = json.loads(path.read_text())
            responses.append(payload["response"])
            url = payload.get("endpoint") or url
            continue
        if fetched:
            time.sleep(SLEEP_BETWEEN_PARTS)
        fetched += 1
        started = time.time()
        query = bbox_query(query_for(box), boxes)
        url, response = request_json(endpoints, data={"data": query},
                                     validate=count_check(expected), log=log)
        payload = {"bbox": list(box), "endpoint": url, "seconds": round(time.time() - started, 1),
                   "expected_beaches": expected, "query": query, "response": response}
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False))
        tmp.replace(path)
        responses.append(response)
        log(f"    {tile.id} {kind} part {k}/{len(todo)} {box}: "
            f"{sum(1 for e in response.get('elements', []) if e.get('type') != 'count')} "
            f"elements, {payload['seconds']}s")
    return url, merge(responses), len(todo)
