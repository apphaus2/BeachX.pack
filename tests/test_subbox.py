import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from beachx_pack import nearby, overpass, subbox, tiles, water


def count(n: int, w: int, r: int) -> dict:
    return {"type": "count", "id": 0,
            "tags": {"nodes": str(n), "ways": str(w), "relations": str(r), "total": str(n + w + r)}}


TILE = tiles.Tile("CA_32N_119W", 32.0, -119.0, 34.0, -117.0, "CA", "US", "pacific")


class PartTests(unittest.TestCase):
    def test_cells_touched_by_beach_bounds(self):
        els = [
            {"type": "node", "id": 1, "lat": 32.71, "lon": -117.24},
            {"type": "way", "id": 2, "bounds": {"minlat": 32.97, "minlon": -117.27,
                                               "maxlat": 33.01, "maxlon": -117.26}},  # crosses 33
            {"type": "relation", "id": 3, "bounds": {"minlat": 35.0, "minlon": -117.5,
                                                    "maxlat": 35.1, "maxlon": -117.4}},  # outside
            {"type": "beachx_area", "id": 0, "tags": {}},
        ]
        got = subbox.parts(TILE.bbox, els)
        self.assertEqual([p[0] for p in got], [(32.5, -117.5, 33.0, -117.0),
                                               (33.0, -117.5, 33.5, -117.0)])
        self.assertEqual([len(p[1]) for p in got], [2, 1])
        self.assertEqual([p[2] for p in got], [2, 0])  # the way's centre is south of 33
        self.assertEqual(subbox.parts(TILE.bbox, []), [])

    def test_a_beach_on_the_tile_edge_lands_in_the_last_cell(self):
        els = [{"type": "node", "id": 1, "lat": 34.0, "lon": -117.0}]
        self.assertEqual([p[0] for p in subbox.parts(TILE.bbox, els)],
                         [(33.5, -117.5, 34.0, -117.0)])

    def test_widen_merges_neighbours_but_not_a_long_diagonal(self):
        near = subbox.widen([(33.0, -117.3, 33.001, -117.299), (33.001, -117.299, 33.002, -117.298)],
                            300)
        self.assertEqual(len(near), 1)
        self.assertLess(near[0][0], 33.0 - 0.0027)
        apart = subbox.widen([(33.0, -117.3, 33.001, -117.299), (33.5, -117.8, 33.501, -117.799)],
                             300)
        self.assertEqual(len(apart), 2)
        # two long boxes that touch only at a corner would make a big union: kept apart
        diag = subbox.widen([(33.0, -117.3, 33.05, -117.25), (33.05, -117.25, 33.1, -117.2)], 0)
        self.assertEqual(len(diag), 2)


class QueryTests(unittest.TestCase):
    BOXES = [(32.7, -117.25, 32.71, -117.24)]

    def check(self, query: str, radius: int, filters: int):
        got = subbox.bbox_query(query, self.BOXES)
        self.assertNotIn("around", got)
        lines = [l for l in got.splitlines() if l.startswith("  ")]
        self.assertEqual(len(lines), filters)
        widened = subbox.widen(self.BOXES, radius)[0]
        self.assertIn(f"({widened[0]:.6f},{widened[1]:.6f},{widened[2]:.6f},{widened[3]:.6f})",
                      lines[0])
        self.assertEqual(got.count("out count;"), 2)
        self.assertIn(".b;\nout count;", got)

    def test_nearby_query_by_box(self):
        q = nearby.build_query((32.5, -117.5, 33.0, -117.0))
        self.check(q, 300, q.count("(around.b:300)"))

    def test_water_query_by_box(self):
        q = water.build_query((32.5, -117.5, 33.0, -117.0))
        self.check(q, 500, q.count("(around.b:500)"))

    def test_an_unknown_around_line_is_refused(self):
        with self.assertRaises(ValueError):
            subbox.bbox_query("node(around.x:5);\n", self.BOXES)


class MergeTests(unittest.TestCase):
    def test_merge_dedupes_and_sums_the_counts(self):
        a = {"elements": [{"type": "node", "id": 5}, {"type": "way", "id": 5}, count(1, 2, 0),
                          count(1, 2, 0)]}
        b = {"elements": [{"type": "way", "id": 5}, {"type": "way", "id": 6}, count(0, 3, 1),
                          count(0, 3, 1)]}
        got = subbox.merge([a, b])
        self.assertEqual([(e["type"], e["id"]) for e in got["elements"] if e["type"] != "count"],
                         [("node", 5), ("way", 5), ("way", 6)])
        counts = [e["tags"] for e in got["elements"] if e["type"] == "count"]
        self.assertEqual(counts, [{"nodes": "1", "ways": "5", "relations": "1", "total": "7"}] * 2)
        nearby.check_canary(got)
        self.assertNotIn("osm3s", got)
        a["osm3s"] = {"timestamp_osm_base": "2026-10-21T05:00:00Z"}
        b["osm3s"] = {"timestamp_osm_base": "2026-10-21T04:58:00Z"}
        self.assertEqual(subbox.merge([a, b])["osm3s"], b["osm3s"])

    def test_count_check(self):
        check = subbox.count_check(10)
        check({"elements": [count(0, 9, 0), count(0, 9, 0)]})
        with self.assertRaises(overpass.FetchError):
            check({"elements": [count(0, 3, 0), count(0, 3, 0)]})
        with self.assertRaises(overpass.FetchError):
            check({"elements": []})


class FetchTests(unittest.TestCase):
    def test_fetch_tile_asks_each_part_once_and_resumes(self):
        t = TILE
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)
            overpass.write_payload(overpass.tile_path(raw, t, "osm"), {"response": {"elements": [
                {"type": "node", "id": 1, "lat": 32.71, "lon": -117.24},
                {"type": "node", "id": 2, "lat": 33.9, "lon": -118.9}]}})
            asked = []

            def answer(urls, data=None, validate=None, log=None, **_):
                asked.append(data["data"])
                if len(asked) == 2:
                    raise overpass.FetchError("gateway timeout")
                resp = {"elements": [{"type": "node", "id": 100 + len(asked), "tags": {},
                                      "lat": 32.7, "lon": -117.2}, count(1, 0, 0), count(1, 0, 0)]}
                validate(resp)
                return urls[0], resp

            with mock.patch.object(subbox, "request_json", side_effect=answer), \
                    mock.patch.object(subbox, "SLEEP_BETWEEN_PARTS", 0):
                with self.assertRaises(overpass.FetchError):
                    overpass.fetch_tile(t, raw, ["https://example"], "osm_nearby",
                                        log=lambda *_: None)
                self.assertFalse(overpass.tile_path(raw, t, "osm_nearby").exists())
                payload = overpass.fetch_tile(t, raw, ["https://example"], "osm_nearby",
                                              log=lambda *_: None)
            self.assertEqual(len(asked), 3)  # the first part was kept, not asked again
            self.assertIn("(32.5,-117.5,33.0,-117.0)", asked[0])  # the cell's beaches
            self.assertNotIn("around", asked[0])
            els = payload["response"]["elements"]
            self.assertEqual(sum(1 for e in els if e["type"] == "node"), 2)
            self.assertEqual([e["tags"]["total"] for e in els if e["type"] == "count"], ["2", "2"])
            self.assertTrue(json.loads(overpass.tile_path(raw, t, "osm_nearby").read_text()))
            overpass.summarize(payload, "osm_nearby")


if __name__ == "__main__":
    unittest.main()
