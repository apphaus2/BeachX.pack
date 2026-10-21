import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from beachx_pack.sources import gazetteer, padus


def rec(rid: str, lat: float, lng: float) -> dict:
    return {"id": rid, "state": "CA", "_point": (lat, lng), "latitude": lat, "longitude": lng}


class LaterTilesTests(unittest.TestCase):
    """A sources pass run while the Overpass queue is still writing tiles must
    not leave the beaches of later tiles without the local lookups."""

    def test_gazetteer_answers_again_when_beaches_arrive(self):
        places = [{"name": "Coronado", "geoid": "0616378", "lat": 32.68, "lng": -117.18}]
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(gazetteer, "load_places", return_value=places) as load:
            raw = Path(tmp)
            first = [rec("beach_osm_w1", 32.68, -117.19)]
            gazetteer.pull(raw, first, {}, log=lambda *_: None)
            gazetteer.pull(raw, first, {}, log=lambda *_: None)
            self.assertEqual(load.call_count, 1)  # nothing new: cached
            later = first + [rec("beach_osm_w2", 32.69, -117.18)]
            gazetteer.pull(raw, later, {}, log=lambda *_: None)
            self.assertEqual(load.call_count, 2)
            got = gazetteer.fresh(raw, later)
            self.assertEqual(got["beach_osm_w2"]["nearest_place"], "Coronado")

    def test_padus_answers_again_when_beaches_arrive(self):
        ring = [(32.6, -117.3), (32.6, -117.1), (32.8, -117.1), (32.8, -117.3), (32.6, -117.3)]
        units = [{"attrs": {"Category": "Fee", "Unit_Nm": "Silver Strand", "Pub_Access": "OA",
                            "MngTp_Desc": "State", "MngNm_Desc": "State Park",
                            "DesTp_Desc": "State Park", "GIS_Acres": 10.0},
                  "polygons": [[ring]], "bbox": (32.6, -117.3, 32.8, -117.1)}]
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(padus, "load_units", return_value=(units, "x.zip")) as load:
            raw = Path(tmp)
            first = [rec("beach_osm_w1", 32.68, -117.19)]
            padus.pull(raw, first, {}, log=lambda *_: None)
            self.assertEqual(padus.pull(raw, first, {}, log=lambda *_: None)["cached"], 1)
            later = first + [rec("beach_osm_w2", 32.7, -117.2)]
            padus.pull(raw, later, {}, log=lambda *_: None)
            self.assertEqual(load.call_count, 2)
            self.assertEqual(padus.fresh(raw, later)["beach_osm_w2"]["padus_unit_name"],
                             "Silver Strand")


if __name__ == "__main__":
    unittest.main()
