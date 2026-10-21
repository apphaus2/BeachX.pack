import json
import math
import struct
import unittest
import zlib
from pathlib import Path

from beachx_pack import cells, geotiff

FIXTURE = Path(__file__).parent / "rows_fixture.json"


class RecordTests(unittest.TestCase):
    def test_record_is_eight_little_endian_bytes(self):
        self.assertEqual(cells.RECORD.size, 8)
        blob = cells.encode([(-3, 7, 2)], [(12, 0, 0, 4, 1), (-32768, 32767, 255, 255, 2)])
        self.assertEqual(len(blob), 6 + 2 * 8)
        self.assertEqual(blob[:6], struct.pack("<hhH", -3, 7, 2))
        self.assertEqual(blob[6:14], bytes([12, 0, 0, 0, 0, 4, 1, 0]))
        self.assertEqual(blob[14:22], bytes([0x00, 0x80, 0xFF, 0x7F, 255, 255, 2, 0]))

    def test_rows_blob_round_trip_with_two_runs_in_a_row(self):
        rows = [[-1, [[0, 2]]], [0, [[-2, -1], [3, 3]]]]
        runs = cells.runs_of(rows)
        self.assertEqual(runs, [(-1, 0, 3), (0, -2, 2), (0, 3, 1)])
        recs = [(i, 10 * i, cells.DRY, i, 0) for i in range(6)]
        back_runs, back = cells.decode(cells.encode(runs, recs))
        self.assertEqual((back_runs, back), (runs, recs))
        self.assertEqual(cells.spans_of(back_runs), rows)
        with self.assertRaises(ValueError):
            cells.encode(runs, recs[:5])

    def test_fixture_dump_matches_its_blob(self):
        """tests/rows_fixture.json: one small Hawaii beach's rows written out
        as JSON beside the base64 blob the pack stores."""
        fx = json.loads(FIXTURE.read_text())
        runs = [(r["y"], r["x0"], len(r["cells"])) for r in fx["rows"]]
        recs = [tuple(c[k] for k in ("height", "water", "volatility", "slope", "flags"))
                for r in fx["rows"] for c in r["cells"]]
        self.assertEqual(cells.b64(cells.encode(runs, recs)), fx["grid"]["rows"])
        self.assertEqual(len(recs), fx["grid"]["cell_count"])
        self.assertEqual(fx["grid"]["cell_record_version"], 1)


class DeriveTests(unittest.TestCase):
    def grid(self, n_rows: int, width: int = 3) -> dict:
        rows = [[y, [[0, width - 1]]] for y in range(n_rows)]
        count = n_rows * width
        return {"anchor": {"latitude": 0, "longitude": 0, "heading_degrees": 0},
                "cell_size_m": 5, "cell_count": count, "x_min": 0, "x_max": width - 1,
                "y_min": 0, "y_max": n_rows - 1, "rows": rows,
                "water_dm": [0 if y == 0 else y * 50 for y in range(n_rows) for _ in range(width)],
                "edge": [x in (0, width - 1) for y in range(n_rows) for x in range(width)]}

    def test_bands_rise_from_the_water_and_back_starts_at_the_dune_face(self):
        # heights (cm, LMSL): a gentle beach face, a steep dune face, a dune top
        profile = [0, 30, 60, 90, 120, 400, 420]
        g = self.grid(len(profile))
        heights = [h for h in profile for _ in range(3)]
        rec = {"water_kind": "sea", "terrain_datum": "LMSL", "tide_mhw_above_msl_m": 0.2,
               "tide_hat_above_mhw_m": 0.4, "wave_margin_m": 0.5, "surface": "sand"}
        recs, numbers = cells.records(g, rec, heights)
        self.assertEqual(numbers, {"datum_offset_m": -0.2, "wet_limit_m": 0.9,
                                   "tide_reference": "MHW over MSL"})
        vol = [recs[i * 3 + 1][2] for i in range(len(profile))]
        self.assertEqual(vol, [cells.WASH, cells.WET, cells.WET, cells.WET, cells.DRY,
                               cells.BACK, cells.BACK])
        self.assertEqual([recs[i * 3 + 1][0] for i in range(len(profile))],
                         [0, 3, 6, 9, 12, 40, 42])
        self.assertEqual(recs[4][3], 3)  # atan(0.3 / 5)
        self.assertEqual(recs[4 * 3 + 1][3], 29)  # the dune face: atan(2.8 / 5)
        middle = [recs[i * 3 + 1][4] for i in range(len(profile))]
        self.assertEqual([f & cells.BUILD for f in middle], [0, 1, 1, 1, 0, 0, 0])
        self.assertTrue(all(recs[i * 3][4] & cells.EDGE for i in range(len(profile))))
        self.assertTrue(all(not recs[i * 3][4] & cells.BUILD for i in range(len(profile))))

    def test_unknown_terrain_and_no_water(self):
        g = self.grid(2)
        g["water_dm"] = [None] * 6
        recs, numbers = cells.records(g, {"water_kind": None}, None)
        self.assertEqual(set(recs), {(cells.HEIGHT_UNKNOWN, cells.WATER_MAX, cells.UNKNOWN,
                                      cells.SLOPE_UNKNOWN, r[4]) for r in recs})
        self.assertIsNone(numbers["datum_offset_m"])

    def test_island_lidar_is_read_against_its_flattened_sea(self):
        from beachx_pack.assemble import _sea_levels
        recs = [{"terrain_datum": "LMSL", "water_kind": "sea", "terrain_project": "big",
                 "terrain_water_m": w} for w in (-0.4, -0.4, -0.4, 0.3, -0.4, 2.1)]
        recs.append({"terrain_datum": "NAVD88", "water_kind": "sea", "terrain_project": "big",
                     "terrain_water_m": -0.4})
        _sea_levels(recs)
        self.assertEqual([r["terrain_sea_level_m"] for r in recs], [-0.4] * 6 + [None])
        g = self.grid(2)
        rec = {**recs[0], "tide_mhw_above_msl_m": 0.2, "tide_hat_above_mhw_m": 0.4,
               "wave_margin_m": 0.5, "surface": "sand"}
        _, numbers = cells.records(g, rec, [-40] * 3 + [0] * 3)
        self.assertEqual(numbers["datum_offset_m"], -0.2)  # -0.4 is the sea: MHW sits at -0.2
        self.assertEqual(numbers["tide_reference"], "MHW over the flattened sea")

    def test_navd88_lidar_without_a_navd88_tie_has_no_bands(self):
        g = self.grid(2)
        rec = {"water_kind": "sea", "terrain_datum": "NAVD88", "tide_mhw_above_msl_m": 0.58,
               "tide_mhw_above_navd88_m": None, "tide_hat_above_mhw_m": 0.8,
               "wave_margin_m": 0.5, "surface": "sand"}
        recs, numbers = cells.records(g, rec, [100] * 3 + [200] * 3)
        self.assertEqual(numbers, {"datum_offset_m": None, "wet_limit_m": None,
                                   "tide_reference": None})
        self.assertEqual({r[2] for r in recs}, {cells.UNKNOWN})
        rec["tide_mhw_above_navd88_m"] = 1.37
        _, numbers = cells.records(g, rec, [100] * 3 + [200] * 3)
        self.assertEqual(numbers, {"datum_offset_m": -0.37, "wet_limit_m": 1.3,
                                   "tide_reference": "MHW over NAVD88"})

    def test_lake_uses_the_water_surface(self):
        g = self.grid(3)
        rec = {"water_kind": "lake", "terrain_water_m": 330.0, "terrain_lowest_m": 329.9,
               "wave_margin_m": 0.3, "surface": None}
        recs, numbers = cells.records(g, rec, [32990] * 3 + [33020] * 3 + [33100] * 3)
        self.assertEqual(numbers["tide_reference"], "water surface at flight")
        self.assertEqual([recs[i][2] for i in (1, 4, 7)], [cells.WASH, cells.WET, cells.DRY])


def tiny_tiff(values, width, height, x0, y0, step, compression=8):
    """A one-strip float32 GeoTIFF, little-endian."""
    data = struct.pack(f"<{width * height}f", *values)
    if compression == 8:
        data = zlib.compress(data)
    entries = []  # (tag, type, count, payload bytes)
    def short(tag, v): entries.append((tag, 3, 1, struct.pack("<HH", v, 0)))
    def long(tag, v): entries.append((tag, 4, 1, struct.pack("<I", v)))
    short(256, width); short(257, height); short(258, 32); short(259, compression)
    short(277, 1); short(278, height); short(339, 3)
    extra = [(273, 4, 1, None), (279, 4, 1, struct.pack("<I", len(data))),
             (33550, 12, 3, struct.pack("<3d", step, step, 0)),
             (33922, 12, 6, struct.pack("<6d", 0, 0, 0, x0, y0, 0))]
    n = len(entries) + len(extra)
    ifd_size = 2 + 12 * n + 4
    blob_at = 8 + ifd_size
    blobs = b""
    out = []
    for tag, typ, count, payload in entries:
        out.append(struct.pack("<HHI", tag, typ, count) + payload)
    offsets = {}
    for tag, typ, count, payload in extra:
        if payload is not None and len(payload) > 4:
            offsets[tag] = blob_at + len(blobs)
            blobs += payload
    strip_at = blob_at + len(blobs)
    for tag, typ, count, payload in extra:
        if tag == 273:
            out.append(struct.pack("<HHII", tag, typ, count, strip_at))
        elif tag in offsets:
            out.append(struct.pack("<HHII", tag, typ, count, offsets[tag]))
        else:
            out.append(struct.pack("<HHI", tag, typ, count) + payload)
    out.sort(key=lambda e: struct.unpack("<H", e[:2])[0])
    head = b"II*\x00" + struct.pack("<I", 8)
    return head + struct.pack("<H", n) + b"".join(out) + struct.pack("<I", 0) + blobs + data


class GeotiffTests(unittest.TestCase):
    def test_reads_deflate_and_plain_strips_and_samples_bilinearly(self):
        for compression in (1, 8):
            data = tiny_tiff([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, -3.4e38, 7.0, 8.0], 3, 3,
                             100.0, 50.0, 10.0, compression)
            r = geotiff.read(data)
            self.assertEqual((r.width, r.height, r.x0, r.y0, r.sx), (3, 3, 100.0, 50.0, 10.0))
            self.assertTrue(math.isnan(r.at(0, 2)))  # float32 no-data
            self.assertAlmostEqual(r.sample(105.0, 45.0), 0.0)  # first pixel centre
            self.assertAlmostEqual(r.sample(110.0, 40.0), 2.0)  # between 0, 1, 3, 4
            self.assertTrue(math.isnan(r.sample(105.0, 25.0)))  # touches the no-data pixel

    def test_lzw(self):
        # "ABABABA" as 9-bit TIFF LZW codes: clear, A, B, 258 (AB), 260 (ABA), end
        codes = [256, 65, 66, 258, 260, 257]
        bits = "".join(format(c, "09b") for c in codes)
        bits += "0" * (-len(bits) % 8)
        packed = bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))
        self.assertEqual(geotiff._lzw_decode(packed), b"ABABABA")


if __name__ == "__main__":
    unittest.main()
