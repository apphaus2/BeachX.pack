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
        self.assertEqual(len(ks), 3 * 5)  # the 2 km pad reaches past 40 N
        self.assertEqual(ks[0].id, "KS_36N_104W")
        self.assertTrue(all(t.region == "inland" and t.state == "KS" for t in ks))
        self.assertIn("KS", tiles.states_for_tile(ks[0]))
        self.assertIn("CO", tiles.states_for_tile(ks[0]))

    def test_generated_tiles_are_clipped_to_the_state_box(self):
        # The real TIGER box: Kansas reaches 40.003 N, so a third row of tiles
        # exists, but only as a thin strip, not a 2-degree row of Nebraska.
        ks = tiles.tiles_for_state("KS", (36.993016, -102.051769, 40.003166, -94.588387))
        self.assertEqual(len(ks), 3 * 5)
        north = [t for t in ks if t.id.startswith("KS_40N")]
        self.assertTrue(all(t.north - t.south < 0.05 for t in north))
        first = ks[0]
        self.assertEqual(first.id, "KS_36N_104W")
        self.assertAlmostEqual(first.south, 36.963016)
        self.assertAlmostEqual(first.west, -102.081769)
        self.assertEqual((first.north, first.east), (38.0, -102.0))
        for t in ks:
            self.assertLessEqual(t.north - t.south, 2.0)
            self.assertLessEqual(t.east - t.west, 2.0)

    def test_inland_time_zones(self):
        self.assertEqual(timezone.zone("KS", 38.8, -97.6), "America/Chicago")
        self.assertEqual(timezone.zone("KS", 38.5, -101.8), "America/Denver")
        self.assertEqual(timezone.zone("KS", 37.2, -101.8), "America/Chicago")  # Morton County


if __name__ == "__main__":
    unittest.main()


class InlandStationTests(unittest.TestCase):
    """A lake or inland beach is never tied to a tide station or buoy on a far coast."""

    def setUp(self):
        import json
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = Path(self.tmp.name)
        (self.raw / "tides").mkdir()
        (self.raw / "tides" / "stations.json").write_text(json.dumps({"response": {"stations": [
            {"id": "8771450", "name": "Galveston", "lat": 29.31, "lng": -94.79},
            {"id": "8775870", "name": "Corpus Christi", "lat": 27.58, "lng": -97.22}]}}))
        (self.raw / "buoys").mkdir()
        (self.raw / "buoys" / "station_table.json").write_text(json.dumps({"response": {"text": (
            "42035|NDBC|3-meter foundation buoy||Galveston||29.232 N 94.413 W (x)|C|\n"
            "45007|NDBC|3-meter discus buoy||South Michigan||42.674 N 87.026 W (x)|C|\n")}}))

    def tearDown(self):
        self.tmp.cleanup()

    def fresh(self, recs):
        from beachx_pack.sources import buoys as b, tides as t
        return t.fresh(self.raw, recs), b.fresh(self.raw, recs)

    def test_kansas_lake_and_unknown_water_take_no_station(self):
        recs = [{"id": "lake", "latitude": 39.25, "longitude": -96.9, "water_kind": "lake",
                 "_regions": {"inland"}},
                {"id": "dry", "latitude": 38.8, "longitude": -97.6, "water_kind": None,
                 "_regions": {"inland"}}]
        tide, buoy = self.fresh(recs)
        for rid in ("lake", "dry"):
            self.assertIsNone(tide[rid]["tide_station_id"])
            self.assertIsNone(buoy[rid]["wave_buoy_id"])

    def test_sea_and_near_unknown_water_keep_their_station(self):
        recs = [{"id": "sea", "latitude": 29.0, "longitude": -96.0, "water_kind": "sea",
                 "_regions": {"gulf"}},
                {"id": "near", "latitude": 29.28, "longitude": -94.82, "water_kind": None,
                 "_regions": {"gulf"}},
                {"id": "lakeside", "latitude": 42.9, "longitude": -87.85, "water_kind": "lake",
                 "_regions": {"great_lakes"}}]
        tide, buoy = self.fresh(recs)
        self.assertEqual(tide["sea"]["tide_station_id"], "8771450")
        self.assertEqual(tide["near"]["tide_station_id"], "8771450")
        self.assertEqual(buoy["near"]["wave_buoy_id"], "42035")
        self.assertIsNone(tide["lakeside"]["tide_station_id"])
        self.assertEqual(buoy["lakeside"]["wave_buoy_id"], "45007")  # a Great Lakes buoy
