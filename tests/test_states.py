import json
import struct
import tempfile
import unittest
from pathlib import Path

from beachx_pack import states, tiles
from beachx_pack.pipeline import build_pack
from beachx_pack.sources import timezone
from tests.test_pack import beach_tiles, write_raw


def square(s, w, n, e):
    return [(s, w), (n, w), (n, e), (s, e), (s, w)]


class StateTests(unittest.TestCase):
    def setUp(self):
        self.hi = states.State("HI", "Hawaii", "15", [square(21.0, -158.5, 22.0, -157.0)])
        self.ca = states.State("CA", "California", "06", [square(21.0, -157.0, 22.0, -156.0)])
        self.every = {"HI": self.hi, "CA": self.ca}

    def test_point_in_state_near_and_elsewhere(self):
        self.assertEqual(states.assign(21.3, -157.8, self.hi, self.every), "tiger")
        self.assertEqual(states.assign(22.01, -157.8, self.hi, self.every), "tiger_near")  # 1.1 km out
        self.assertIsNone(states.assign(22.1, -157.8, self.hi, self.every))  # 11 km out
        self.assertIsNone(states.assign(21.5, -156.5, self.hi, self.every))  # in another state

    def test_shapefile_and_dbase_readers(self):
        ring = square(21.0, -158.0, 22.0, -157.0)
        pts = b"".join(struct.pack("<2d", lng, lat) for lat, lng in ring)
        body = struct.pack("<i4dii", 5, 0, 0, 0, 0, 1, len(ring)) + struct.pack("<i", 0) + pts
        shp = b"\0" * 100 + struct.pack(">ii", 1, len(body) // 2) + body
        self.assertEqual(states.read_shp(shp), {0: [ring]})
        fields = b"STUSPS" + b"\0" * 5 + b"C" + b"\0" * 4 + bytes([2]) + b"\0" * 15
        head = bytes([3, 126, 1, 1]) + struct.pack("<IHH", 1, 32 + 32 + 1, 3) + b"\0" * 20
        dbf = head + fields + b"\r" + b" HI"
        self.assertEqual(states.read_dbf(dbf), [{"STUSPS": "HI"}])

    def test_build_keeps_only_the_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, pack = Path(tmp, "raw"), Path(tmp, "pack", "us", "hi")
            write_raw(raw, beach_tiles())
            keep = lambda rec: ("HI", "US", "tiger") if rec["id"] == "beach_osm_w1" else None  # noqa: E731
            man, _ = build_pack(raw, pack, "2026-10-20", keep=keep, state="HI")
            ids = [r["id"] for r in json.loads((pack / "beaches.json").read_text())]
            self.assertEqual(ids, ["beach_osm_w1"])
            self.assertEqual(man["counts"]["geometry_pipeline"]["other_state"], 1)
            self.assertEqual((man["state"], man["country"]), ("HI", "US"))
            for name in ("beaches.csv", "boundaries.json", "grids.json", "nearby.json",
                         "manifest.json"):
                self.assertTrue((pack / name).exists(), name)


class TileTests(unittest.TestCase):
    def test_coastal_state_keeps_its_tiles_inland_state_gets_generated_ones(self):
        self.assertEqual(tiles.tiles_for_state("HI"), tiles.select_tiles("hawaii"))
        ks = tiles.tiles_for_state("KS", (36.99, -102.05, 40.00, -94.59))
        self.assertEqual(len(ks), 2 * 5)
        self.assertEqual(ks[0].id, "KS_36N_104W")
        self.assertTrue(all(t.region == "inland" and t.state == "KS" for t in ks))
        self.assertIn("KS", tiles.states_for_tile(ks[0]))
        self.assertIn("CO", tiles.states_for_tile(ks[0]))

    def test_inland_time_zones(self):
        self.assertEqual(timezone.zone("KS", 38.8, -97.6), "America/Chicago")
        self.assertEqual(timezone.zone("KS", 38.5, -101.8), "America/Denver")
        self.assertEqual(timezone.zone("KS", 37.2, -101.8), "America/Chicago")  # Morton County


if __name__ == "__main__":
    unittest.main()
