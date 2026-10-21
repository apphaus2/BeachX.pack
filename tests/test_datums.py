import unittest

from beachx_pack.sources import datums

STATIONS = {
    "A": {"id": "A", "lat": 32.70, "lng": -117.20, "reference_id": None},   # no NAVD 88, no HAT
    "B": {"id": "B", "lat": 32.75, "lng": -117.20, "reference_id": None},   # 5.6 km: NAVD 88 only
    "C": {"id": "C", "lat": 32.90, "lng": -117.20, "reference_id": None},   # 22 km: everything
    "S": {"id": "S", "lat": 32.80, "lng": -117.21, "reference_id": "C"},    # subordinate of C
    "F": {"id": "F", "lat": 34.00, "lng": -117.20, "reference_id": None},   # 145 km away
    "L": {"id": "L", "lat": 33.50, "lng": -118.00, "reference_id": None},   # lonely, nothing near
}
VALUES = {
    "A": {"MHW": 1.9, "MSL": 1.3},
    "B": {"MHW": 2.0, "MSL": 1.4, "NAVD88": 0.62},
    "C": {"MHW": 2.1, "MSL": 1.5, "NAVD88": 0.70, "HAT": 2.9},
    "F": {"MHW": 2.0, "MSL": 1.4, "NAVD88": 0.6, "HAT": 2.8},
    "L": {"MHW": 1.8},
}


class FillTests(unittest.TestCase):
    def setUp(self):
        self.asked = []

    def datums_of(self, sid):
        self.asked.append(sid)
        return VALUES.get(sid)

    def test_station_with_everything_answers_alone(self):
        got = datums.fill("C", STATIONS, self.datums_of)
        self.assertEqual(got["tide_mhw_above_navd88_m"], 1.4)
        self.assertEqual(got["tide_hat_above_mhw_m"], 0.8)
        self.assertEqual({got[k] for k in ("tide_datum_station_id", "tide_navd88_station_id",
                                           "tide_hat_station_id")}, {"C"})
        self.assertEqual(self.asked, ["C"])  # no neighbour asked

    def test_missing_differences_come_from_the_nearest_neighbour_that_has_them(self):
        got = datums.fill("A", STATIONS, self.datums_of)
        self.assertEqual(got["tide_datum_station_id"], "A")
        self.assertEqual(got["tide_mhw_above_msl_m"], 0.6)
        self.assertEqual(got["tide_navd88_station_id"], "B")  # nearest with NAVD 88
        self.assertEqual(got["tide_mhw_above_navd88_m"], 1.38)
        self.assertEqual(got["tide_hat_station_id"], "C")  # B has no HAT, C does
        self.assertNotIn("F", self.asked)  # stopped once everything was found

    def test_a_subordinate_station_answers_with_its_reference(self):
        got = datums.fill("S", STATIONS, self.datums_of)
        self.assertEqual(got["tide_datum_station_id"], "C")
        self.assertEqual(got["tide_hat_station_id"], "C")

    def test_nothing_within_reach_stays_null(self):
        got = datums.fill("L", STATIONS, self.datums_of)
        self.assertIsNone(got["tide_mhw_above_navd88_m"])
        self.assertIsNone(got["tide_navd88_station_id"])
        self.assertIsNone(got["tide_datum_station_id"])
        self.assertNotIn("F", self.asked)  # beyond FILL_KM

    def test_neighbours_are_nearest_first_and_within_reach(self):
        self.assertEqual(datums.neighbours(STATIONS, "A"), ["B", "S", "C"])


if __name__ == "__main__":
    unittest.main()
