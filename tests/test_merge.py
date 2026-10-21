import unittest

from beachx_pack import normalize
from beachx_pack.geometry import from_local
from tests.fixtures import LAT0, LNG0, marker, rect, tile, way


def node(osm_id, east, north, tags):
    lat, lng = from_local(east, north, LAT0, LNG0)
    return {"type": "node", "id": osm_id, "lat": lat, "lon": lng, "tags": tags}


def run(elements):
    members = [f"{e['type']}/{e['id']}" for e in elements]
    t = tile("HI_A", elements + [marker("US", members), marker("US-HI", members)])
    geo, report = normalize.geometry_blocks([t], {})
    return geo, report


class MergeTests(unittest.TestCase):
    def test_same_sand_mapped_twice_is_one_beach(self):
        a = way(1, rect(200, 40, 30), {"natural": "beach", "name": "Long Beach"})
        b = way(2, rect(190, 38, 30, offset=(3, 0)), {"natural": "beach", "surface": "sand"})
        geo, report = run([a, b])
        self.assertEqual(list(geo), ["beach_osm_w1"])
        core = geo["beach_osm_w1"]["data"]["core"]
        self.assertEqual(core["osm_ids"], ["way/1", "way/2"])
        self.assertEqual(core["surface"], "sand")  # filled from the other member
        self.assertEqual(core["merged_from"][0]["osm_id"], 2)
        self.assertEqual(report["merged_beaches"], 1)
        shp = geo["beach_osm_w1"]["data"]["shape"]
        self.assertEqual(len(shp["boundary"]["rings"]), 2)
        self.assertLess(abs(shp["area_m2"] - 8000) / 8000, 0.1)  # union, not the sum

    def test_adjacent_beaches_stay_separate(self):
        a = way(1, rect(100, 40, 0), {"natural": "beach", "name": "A"})
        b = way(2, rect(100, 40, 0, offset=(0, 100)), {"natural": "beach", "name": "B"})
        geo, _ = run([a, b])
        self.assertEqual(sorted(geo), ["beach_osm_w1", "beach_osm_w2"])

    def test_different_names_never_merge(self):
        a = way(1, rect(200, 40, 0), {"natural": "beach", "name": "A"})
        b = way(2, rect(100, 30, 0), {"natural": "beach", "name": "B"})  # inside A
        geo, _ = run([a, b])
        self.assertEqual(len(geo), 2)

    def test_named_node_names_an_unnamed_polygon(self):
        a = way(1, rect(200, 40, 0), {"natural": "beach"})
        geo, _ = run([a, node(9, 0, 10, {"natural": "beach", "name": "Pin Beach"})])
        self.assertEqual(list(geo), ["beach_osm_w1"])
        self.assertEqual(geo["beach_osm_w1"]["data"]["core"]["name"], "Pin Beach")

    def test_unnamed_stretch_with_several_named_pins_merges_none(self):
        a = way(1, rect(40, 400, 0), {"natural": "beach"})
        pins = [node(8, 0, -100, {"natural": "beach", "name": "North"}),
                node(9, 0, 100, {"natural": "beach", "name": "South"})]
        geo, _ = run([a] + pins)
        self.assertEqual(len(geo), 3)

    def test_merged_grid_is_the_union(self):
        a = way(1, rect(100, 40, 0), {"natural": "beach", "name": "X"})
        b = way(2, rect(100, 40, 0, offset=(0, 30)), {"natural": "beach", "name": "X"})
        geo, _ = run([a, b])
        grid = geo["beach_osm_w1"]["data"]["shape"]["grid"]
        self.assertLess(abs(grid["cell_count"] - 130 * 40 / 25) / (130 * 40 / 25), 0.06)


if __name__ == "__main__":
    unittest.main()
