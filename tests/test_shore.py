import math
import unittest

from beachx_pack import grid, normalize, shore
from beachx_pack.geometry import from_local
from tests.fixtures import LAT0, LNG0, geom, marker, rect, tile, way


def coastline(osm_id, points_en, tags=None):
    pts = [from_local(e, n, LAT0, LNG0) for e, n in points_en]
    return {"type": "way", "id": osm_id, "geometry": geom(pts),
            "tags": tags or {"natural": "coastline"}}


def along(h_deg, a, b):
    """(east, north) of a metres along heading h and b metres across (to the right)."""
    h = math.radians(h_deg)
    return (a * math.sin(h) + b * math.cos(h), a * math.cos(h) - b * math.sin(h))


def build(beach, water):
    t = tile("HI_A", [beach, marker("US", ["way/1"]), marker("US-HI", ["way/1"])])
    w = tile("HI_A", water)
    geo, _ = normalize.geometry_blocks([t], {}, water_tiles=[w])
    return geo["beach_osm_w1"]["data"]["shape"]


class ShoreTests(unittest.TestCase):
    def test_water_edge_turns_y_up_the_beach(self):
        beach = way(1, rect(200, 40, 30), {"natural": "beach"})
        # the coastline runs along the long side 20 m to the right of the axis
        coast = coastline(5, [along(30, -150, 20), along(30, 150, 20)])
        shp = build(beach, [coast])
        self.assertEqual(shp["water"]["kind"], "sea")
        self.assertEqual(shp["water"]["orientation"], "water")
        self.assertAlmostEqual(shp["water"]["edge_m"], 200, delta=1)
        self.assertEqual(len(shp["boundary"]["water_edge"]), 1)
        g = shp["grid"]
        # water to the right of a 30 degree axis is at 120 degrees: up the beach is 300
        self.assertAlmostEqual(g["anchor"]["heading_degrees"], 300.0, delta=0.5)
        cells = shore.cells_of(g)
        touching = [c for c, w in zip(cells, g["water_dm"]) if w == 0]
        self.assertTrue(touching)
        self.assertEqual({c[1] for c in touching}, {min(c[1] for c in cells)})  # lowest row
        far = max(g["water_dm"])
        self.assertAlmostEqual(far / 10, 37.5, delta=0.1)  # top row centre, 40 m wide beach
        self.assertEqual(sorted(set(g["water_dm"]))[:2], [0, 75])  # rows 2.5 and 7.5 m up
        rows = {}
        for (cx, cy), w in zip(cells, g["water_dm"]):
            rows.setdefault(cy, []).append(w)
        means = [sum(v) / len(v) for _, v in sorted(rows.items())]
        self.assertEqual(means, sorted(means))  # farther from the water row by row
        self.assertTrue(any(g["edge"]))
        self.assertFalse(all(g["edge"]))

    def test_beach_drawn_seaward_of_the_coastline(self):
        """The outline meets the coastline on its landward side; the coastline
        keeps land on its left, so the beach is still turned up the beach."""
        beach = way(1, rect(200, 40, 30), {"natural": "beach"})
        coast = coastline(5, [along(30, -150, -20), along(30, 150, -20)])  # land on the left
        shp = build(beach, [coast])
        self.assertAlmostEqual(shp["grid"]["anchor"]["heading_degrees"], 300.0, delta=0.5)
        # the water edge is the outline's seaward side (and its two ends), never the
        # side along the coastline: distances grow up the beach from the sea
        self.assertAlmostEqual(shp["water"]["edge_m"], 280, delta=2)
        g = shp["grid"]
        rows = {}
        for (cx, cy), w in zip(shore.cells_of(g), g["water_dm"]):
            rows.setdefault(cy, []).append(w)
        firsts = [min(v) for _, v in sorted(rows.items())]
        self.assertEqual(firsts[0], 0)
        self.assertEqual(firsts, sorted(firsts))

    def test_lake_polygon_and_no_water(self):
        beach = way(1, rect(100, 30, 0), {"natural": "beach"})
        e0, n0 = along(0, -80, 15)
        e1, n1 = along(0, 80, 15)
        e2, n2 = along(0, 80, 300)
        e3, n3 = along(0, -80, 300)
        lake = coastline(6, [(e0, n0), (e1, n1), (e2, n2), (e3, n3), (e0, n0)],
                         {"natural": "water", "water": "reservoir"})
        shp = build(beach, [lake])
        self.assertEqual(shp["water"]["kind"], "lake")
        self.assertAlmostEqual(shp["grid"]["anchor"]["heading_degrees"], 270.0, delta=0.5)
        dry = build(beach, [])
        self.assertEqual(dry["water"]["orientation"], "outline")
        self.assertTrue(all(w is None for w in dry["grid"]["water_dm"]))

    def test_traverse_marks_every_crossed_cell(self):
        cells = set(shore._traverse(0.5, 0.5, 14.5, 4.5, 5))
        self.assertEqual(cells, {(0, 0), (1, 0), (2, 0)})
        diag = set(shore._traverse(1, 1, 9, 9, 5))
        self.assertTrue({(0, 0), (1, 1)} <= diag)

    def test_heading_from_seaward(self):
        self.assertEqual(shore.heading_from((0, -1), None), 0.0)   # water south: y north
        self.assertEqual(shore.heading_from((1, 0), None), 270.0)  # water east: y west
        self.assertIsNone(shore.heading_from(None, None))


if __name__ == "__main__":
    unittest.main()
