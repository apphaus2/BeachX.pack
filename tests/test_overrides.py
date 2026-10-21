import json
import tempfile
import unittest
from pathlib import Path

from beachx_pack import overrides
from beachx_pack.geometry import from_local
from beachx_pack.pipeline import build_pack
from tests.fixtures import LAT0, LNG0
from tests.test_pack import beach_tiles, write_raw


class OverrideTests(unittest.TestCase):
    def test_shipped_file_is_valid(self):
        path = Path(__file__).resolve().parent.parent / "overrides" / "hi.json"
        self.assertEqual(overrides.load(path), {})

    def test_validation(self):
        good = {"beach_osm_w1": {"tier": "minor", "reason": "r", "date": "2026-10-20"}}
        self.assertEqual(overrides.validate(good), good)
        for bad in ({"x": {"tier": "minor"}},
                    {"x": {"tier": "best", "reason": "r", "date": "d"}},
                    {"x": {"colour": "red", "reason": "r", "date": "d"}},
                    {"x": {"water_edge": [[1, 2]], "reason": "r", "date": "d"}}):
            with self.assertRaises(ValueError):
                overrides.validate(bad)

    def test_name_tier_and_water_edge_are_applied_at_build(self):
        a = from_local(-100, -60, LAT0, LNG0)
        b = from_local(100, -60, LAT0, LNG0)
        ov = {"beach_osm_w1": {"name": "Corrected Beach", "tier": "minor",
                               "water_edge": [[list(a), list(b)]],
                               "reason": "test", "date": "2026-10-20"},
              "beach_osm_w999": {"tier": "primary", "reason": "gone", "date": "2026-10-20"}}
        with tempfile.TemporaryDirectory() as tmp:
            raw, pack = Path(tmp, "raw"), Path(tmp, "pack")
            write_raw(raw, beach_tiles())
            man, _ = build_pack(raw, pack, "2026-10-20", overrides=ov)
            recs = {r["id"]: r for r in json.loads((pack / "beaches.json").read_text())}
            w = recs["beach_osm_w1"]
            self.assertEqual((w["name"], w["display_name"], w["tier"], w["seed_space"]),
                             ("Corrected Beach", "Corrected Beach", "minor", False))
            self.assertEqual(w["override"], {"fields": ["name", "tier", "water_edge"],
                                             "reason": "test", "date": "2026-10-20",
                                             "osm_name": "Test Beach"})
            self.assertEqual(w["orientation"], "override")
            heading = json.loads((pack / "grids.json").read_text())["beach_osm_w1"]["anchor"][
                "heading_degrees"]
            self.assertAlmostEqual(heading, 0.0, delta=0.5)  # water edge to the south: +y north
            self.assertIsNone(recs["beach_osm_n5"]["override"])
            self.assertEqual(man["counts"]["overrides"], {"applied": 1, "unmatched": ["beach_osm_w999"]})
            # a second build without the override goes back to the OSM name
            build_pack(raw, pack, "2026-10-20", overrides={})
            again = {r["id"]: r for r in json.loads((pack / "beaches.json").read_text())}
            self.assertEqual(again["beach_osm_w1"]["name"], "Test Beach")


if __name__ == "__main__":
    unittest.main()
