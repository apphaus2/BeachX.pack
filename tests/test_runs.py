import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from beachx_pack import blocks, cli
from beachx_pack.files import listed


class RunMonthTests(unittest.TestCase):
    def test_status_files_carry_the_day_they_were_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "2026-10"
            blocks.write_status(raw, "osm", {"calls": 1})
            got = json.loads((raw / "_status" / "osm.json").read_text())
            self.assertEqual(got["date"], blocks.today_utc())
            self.assertEqual(got["calls"], 1)

    def test_run_date_is_the_newest_status_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "2026-10"
            blocks.write_status(raw, "osm", {"date": "2026-10-20"})
            blocks.write_status(raw, "census", {"date": "2026-10-21"})
            blocks.write_status(raw, "nws", {"date": "2026-10-03"})
            self.assertEqual(blocks.run_date(raw), "2026-10-21")

    def test_run_date_without_dated_statuses_is_the_first_of_the_month(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "2026-10"
            (raw / "_status").mkdir(parents=True)
            (raw / "_status" / "osm.json").write_text(json.dumps({"source": "osm",
                                                                  "block": "geometry"}))
            self.assertEqual(blocks.run_date(raw), "2026-10-01")

    def test_latest_run_takes_month_folders_with_a_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "raw" / "hi"
            for name in ("2026-09", "2026-10", "2026-11", "2026-13", "2026-10-21"):
                (state / name / "_status").mkdir(parents=True)
            (state / "2026-12").mkdir()  # nothing pulled into it yet
            with mock.patch.object(cli, "RAW_ROOT", root / "raw"), \
                    mock.patch.object(cli, "ROOT", root):
                self.assertEqual(cli._latest_run(cli.Where("HI")), "2026-11")

    def test_day_folders_alone_are_not_a_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "raw" / "ks" / "2026-10-21" / "_status").mkdir(parents=True)
            with mock.patch.object(cli, "RAW_ROOT", root / "raw"), \
                    mock.patch.object(cli, "ROOT", root), mock.patch("builtins.print"):
                self.assertIsNone(cli._latest_run(cli.Where("KS")))

    def test_appledouble_sidecars_are_not_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "2026-10"
            blocks.write_status(raw, "osm", {"date": "2026-10-20"})
            (raw / "_status" / "._osm.json").write_bytes(b"\x00\x05\x16\x07\xb0")
            self.assertEqual(list(blocks.read_statuses(raw)), ["osm"])
            self.assertEqual([p.name for p in listed(raw / "_status", "*.json")], ["osm.json"])

    def test_run_month_must_be_a_month(self):
        self.assertEqual(cli._month("2026-10"), "2026-10")
        for bad in ("2026-10-21", "2026-13", "26-10", "2026-1"):
            with self.assertRaises(SystemExit):
                cli._month(bad)


if __name__ == "__main__":
    unittest.main()
