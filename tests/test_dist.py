import gzip
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from beachx_pack import cells, dist
from beachx_pack.pipeline import build_pack
from tests.test_pack import beach_tiles, write_raw


class SqliteTests(unittest.TestCase):
    def test_dist_writes_tables_index_and_blob(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, pack, out = Path(tmp, "raw"), Path(tmp, "pack", "us", "hi"), Path(tmp, "dist")
            write_raw(raw, beach_tiles())
            man, _ = build_pack(raw, pack, "2026-10-20", state="HI")
            entry = dist.build(pack, out)
            self.assertEqual(entry["path"], "us/hi.sqlite.gz")
            index = json.loads((out / "index.json").read_text())
            self.assertEqual(index["states"][0]["sha256"], entry["sha256"])
            self.assertEqual(index["states"][0]["pack_hash"], man["pack_hash"])
            first = (out / "us" / "hi.sqlite.gz").read_bytes()
            self.assertEqual(dist.build(pack, out)["sha256"], entry["sha256"])  # same bytes
            db_path = Path(tmp, "hi.sqlite")
            db_path.write_bytes(gzip.decompress(first))
            db = sqlite3.connect(db_path)
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(tables, {"manifest", "beaches", "boundaries", "grids", "nearby"})
            idx = db.execute("SELECT sql FROM sqlite_master WHERE name='beaches_latitude_longitude'")
            self.assertIn("latitude, longitude", idx.fetchone()[0])
            self.assertEqual(db.execute("SELECT state, cell_record_version FROM manifest").fetchone(),
                             ("hi", 1))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM beaches").fetchone()[0],
                             man["counts"]["beaches"])
            rid, count, blob = db.execute(
                "SELECT beach_id, cell_count, rows FROM grids").fetchone()
            runs, recs = cells.decode(blob)
            self.assertEqual(len(recs), count)
            self.assertEqual(rid, "beach_osm_w1")
            near = db.execute("SELECT id FROM beaches WHERE latitude BETWEEN 21 AND 22 "
                              "AND longitude BETWEEN -158 AND -157 ORDER BY id").fetchall()
            self.assertEqual([r[0] for r in near], ["beach_osm_n5", "beach_osm_w1"])


if __name__ == "__main__":
    unittest.main()
