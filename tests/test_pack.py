import json
import tempfile
import unittest
from pathlib import Path

from datetime import date

from beachx_pack import blocks, diffpack, grid, hashing, normalize, score, tiles
from beachx_pack.pipeline import build_pack
from beachx_pack.sources import buoys, census, nws, padus, timezone
from beachx_pack.sources.common import Nearest
from beachx_pack.geometry import (clean_ring, join_ways, point_in_polygon, polygon_area_m2,
                                  simplify_ring, from_local)
from tests.fixtures import LAT0, LNG0, marker, rect, tile, way


class TileTests(unittest.TestCase):
    def test_tile_ids_unique_and_hawaii_selection(self):
        self.assertEqual(len({t.id for t in tiles.TILES}), len(tiles.TILES))
        hi = tiles.select_tiles("hawaii")
        self.assertEqual([t.state for t in hi], ["HI"] * 4)
        self.assertEqual(tiles.select_tiles("HI"), hi)
        with self.assertRaises(ValueError):
            tiles.select_tiles("atlantis")

    def test_dedupe_keeps_first_copy(self):
        els = [{"type": "way", "id": 1, "v": "a"}, {"type": "node", "id": 1},
               {"type": "way", "id": 1, "v": "b"}]
        out = tiles.dedupe(els)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["v"], "a")

    def test_state_bbox_lookup(self):
        self.assertEqual(tiles.states_for_point(21.2767, -157.8278), ["HI"])
        self.assertEqual(tiles.states_for_point(25.7826, -80.1341), ["FL"])
        self.assertIn("AK", tiles.states_for_point(52.9, 173.2))  # Attu, east of 180
        self.assertEqual(tiles.states_for_point(0, 0), [])
        self.assertIn("NY", tiles.states_for_point(41.0, -72.0))
        self.assertIn("CT", tiles.states_for_point(41.0, -72.0))  # overlap is expected
        self.assertEqual(tiles.state_code("Hawaii"), "HI")
        self.assertEqual(tiles.state_code("ca"), "CA")
        self.assertIsNone(tiles.state_code("Ontario"))


class IdTests(unittest.TestCase):
    def test_ids_from_osm_element(self):
        self.assertEqual(normalize.beach_id("way", 123), "beach_osm_w123")
        self.assertEqual(normalize.beach_id("relation", 9), "beach_osm_r9")
        self.assertEqual(normalize.beach_id("node", 42), "beach_osm_n42")


class ScoreTests(unittest.TestCase):
    def rec(self, **kw):
        base = {"name": "X Beach", "wikipedia": None, "wikidata": None,
                "wikidata_sitelinks": None, "area_m2": None, "protected_area_hint": False,
                "lifeguard": None, "is_resort": False}
        base.update(kw)
        return base

    def test_points_and_thresholds(self):
        r = self.rec(wikidata="Q1", wikidata_sitelinks=20, area_m2=200_000,
                     protected_area_hint=True, lifeguard="yes", is_resort=True)
        self.assertEqual(score.score(r), 3 + 3 + 2 + 2 + 2 + 1 + 1 + 1 + 1)
        self.assertEqual(score.score(self.rec(wikidata_sitelinks=4, area_m2=49_999)), 3)
        self.assertEqual(score.score(self.rec(lifeguard="seasonal")), 3)

    def test_tiers(self):
        self.assertEqual(score.tier(self.rec(), 8), "primary")
        self.assertEqual(score.tier(self.rec(), 7), "secondary")
        self.assertEqual(score.tier(self.rec(), 4), "secondary")
        self.assertEqual(score.tier(self.rec(), 3), "minor")
        self.assertEqual(score.tier(self.rec(name=None), 12), "minor")


class GeometryTests(unittest.TestCase):
    def test_square_way_area(self):
        ring = clean_ring(rect(100, 100, 0))
        area = polygon_area_m2([ring], [], LAT0, LNG0)
        self.assertAlmostEqual(area, 10_000, delta=10)

    def test_ring_builder_joins_reversed_pieces_and_drops_duplicates(self):
        sq = rect(100, 100, 0)[:-1]
        piece_a = [sq[0], sq[1], sq[1], sq[2]]
        piece_b = [sq[0], sq[3], sq[2]]  # runs the other way
        rings, unclosed = join_ways([piece_a, piece_b])
        self.assertEqual(unclosed, 0)
        self.assertEqual(len(rings), 1)
        self.assertEqual(len(rings[0]), 5)
        self.assertEqual(rings[0][0], rings[0][-1])
        self.assertAlmostEqual(polygon_area_m2(rings, [], LAT0, LNG0), 10_000, delta=10)
        self.assertEqual(join_ways([piece_a])[1], 1)

    def test_simplifier_drops_small_wiggles_keeps_corners(self):
        pts = []
        for i in range(101):  # 100 m edge with 1 m wiggles, then the square's other sides
            pts.append((i, 1.0 if i % 2 else 0.0))
        pts += [(100, 100), (0, 100), (0, 0)]
        ring = [from_local(e, n, LAT0, LNG0) for e, n in pts]
        out = simplify_ring(ring, 3.0, LAT0, LNG0)
        self.assertLessEqual(len(out), 6)
        self.assertEqual(out[0], out[-1])
        for corner in [(100, 100), (0, 100)]:
            self.assertIn(from_local(*corner, LAT0, LNG0), out)
        self.assertAlmostEqual(polygon_area_m2([out], [], LAT0, LNG0), 10_000, delta=150)

    def test_simplifier_keeps_real_shape(self):
        ring = rect(10, 4, 45)
        self.assertEqual(len(simplify_ring(ring, 3.0, LAT0, LNG0)), 5)


class GridTests(unittest.TestCase):
    def test_rotated_rectangle(self):
        ring = clean_ring(rect(200, 40, 30))
        g = grid.build_grid([ring], [])
        # no water: +y across the long axis, so rows run along it
        self.assertAlmostEqual(g["anchor"]["heading_degrees"], 120.0, delta=0.1)
        self.assertLess(abs(g["cell_count"] - 320) / 320, 0.05)
        centres = grid.cell_centres(g)
        self.assertEqual(len(centres), g["cell_count"])
        for lat, lng in centres:
            self.assertTrue(point_in_polygon(lat, lng, [ring], []))

    def test_heading_without_water_wraps_into_0_180(self):
        g = grid.build_grid([clean_ring(rect(200, 40, 210))], [])
        self.assertAlmostEqual(g["anchor"]["heading_degrees"], 120.0, delta=0.1)
        g = grid.build_grid([clean_ring(rect(200, 40, 135))], [])
        self.assertAlmostEqual(g["anchor"]["heading_degrees"], 45.0, delta=0.1)
        g = grid.build_grid([clean_ring(rect(200, 40, 30))], [], heading=300.0)
        self.assertEqual(g["anchor"]["heading_degrees"], 300.0)

    def test_hole_cells_excluded(self):
        outer = clean_ring(rect(120, 100, 0))
        hole = clean_ring(rect(40, 40, 0))
        g = grid.build_grid([outer], [hole])
        for lat, lng in grid.cell_centres(g):
            self.assertTrue(point_in_polygon(lat, lng, [outer], [hole]))
        expected = (120 * 100 - 40 * 40) / 25
        self.assertLess(abs(g["cell_count"] - expected) / expected, 0.05)
        rows = g["rows"]
        self.assertEqual([r[0] for r in rows], sorted(r[0] for r in rows))
        self.assertTrue(any(len(spans) == 2 for _, spans in rows))  # rows split by the hole

    def test_round_trip_frame(self):
        anchor = {"latitude": LAT0, "longitude": LNG0, "heading_degrees": 30.0}
        x, y = grid.to_grid(LAT0 + 0.001, LNG0 + 0.001, anchor)
        lat, lng = grid.from_grid(x, y, anchor)
        self.assertAlmostEqual(lat, LAT0 + 0.001, places=9)
        self.assertAlmostEqual(lng, LNG0 + 0.001, places=9)


def write_raw(raw: Path, payloads: list[dict], kind: str = "osm",
              statuses: tuple[str, ...] = ("osm",)) -> None:
    (raw / kind).mkdir(parents=True, exist_ok=True)
    for p in payloads:
        (raw / kind / f"{p['tile']['id']}.json").write_text(json.dumps(p))
    for src in statuses:
        blocks.write_status(raw, src, {"calls": 0, "ok": 0, "errors": 0, "cached": 0,
                                       "endpoint": None, "note": None, "seconds": 0})


def beach_tiles():
    beach = way(1, rect(400, 150, 30), {"natural": "beach", "name": "Test Beach",
                                        "wikidata": "Q5", "lifeguard": "yes"})
    beach.update(version=7, timestamp="2025-01-02T03:04:05Z", changeset=99, user="x", uid=1)
    node = {"type": "node", "id": 5, "lat": 21.3, "lon": -157.8, "version": 1,
            "tags": {"natural": "beach", "name": "Dot", "wikipedia": "en:Hanauma Bay"}}
    foreign = way(2, rect(100, 50, 0, offset=(5000, 0)), {"natural": "beach"})
    members = ["way/1", "node/5"]
    return [tile("HI_A", [beach, node, foreign, marker("US", members), marker("US-HI", members)]),
            tile("HI_B", [beach, marker("US", ["way/1"]), marker("US-HI", ["way/1"])])]


class BuildTests(unittest.TestCase):
    def test_geometry_block_places_dedupes_and_excludes_foreign(self):
        geo, report = normalize.geometry_blocks(beach_tiles(), {})
        self.assertEqual(report["excluded_foreign"], 1)
        self.assertEqual(sorted(geo), ["beach_osm_n5", "beach_osm_w1"])
        w = geo["beach_osm_w1"]["data"]
        self.assertEqual((w["core"]["state"], w["core"]["state_confidence"]), ("HI", "area"))
        self.assertEqual(w["tiles"], ["HI_A", "HI_B"])
        self.assertAlmostEqual(w["shape"]["area_m2"], 60_000, delta=100)
        self.assertEqual(w["shape"]["boundary"]["point_count"], 5)

    def test_state_falls_back_to_tag_then_bbox(self):
        t = [{"id": "A", "state": "HI", "country": "US"}]
        self.assertEqual(normalize.place({"addr:state": "HI"}, 0, 0, {"US"}, t, True),
                         ("HI", "tag", "US"))
        self.assertEqual(normalize.place({}, 21.3, -157.8, {"US"}, t, True), ("HI", "bbox", "US"))
        self.assertEqual(normalize.place({"addr:state": "NY"}, 41, -72, {"US", "US-NY", "US-CT"},
                                         t, True), ("NY", "tag", "US"))
        self.assertEqual(normalize.place({}, 0, 0, set(), t, True), (None, None, None))

    def test_open_way_has_no_boundary(self):
        el = way(3, rect(100, 20, 0)[:-1], {"natural": "beach"})
        shp = normalize.shape(el, LAT0, LNG0)
        self.assertEqual((shp["area_m2"], shp["boundary"], shp["grid"], shp["note"]),
                         (None, None, None, "open_way"))


class HashAndCacheTests(unittest.TestCase):
    def test_element_hash_is_stable_and_sensitive(self):
        el = way(1, rect(100, 20, 0), {"natural": "beach", "b": "2", "a": "1"})
        same = json.loads(json.dumps(el))
        same["tags"] = {"a": "1", "natural": "beach", "b": "2"}
        self.assertEqual(hashing.element_hash(el), hashing.element_hash(same))
        moved = way(1, rect(100, 20, 0, offset=(1, 0)), el["tags"])
        self.assertNotEqual(hashing.element_hash(el), hashing.element_hash(moved))
        retagged = way(1, rect(100, 20, 0), {**el["tags"], "name": "N"})
        self.assertNotEqual(hashing.element_hash(el), hashing.element_hash(retagged))

    def test_second_build_reuses_everything_and_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, pack = Path(tmp, "raw"), Path(tmp, "pack")
            write_raw(raw, beach_tiles())
            m1, c1 = build_pack(raw, pack, "2026-10-20")
            first = (pack / "beaches.json").read_bytes()
            self.assertEqual(m1["counts"]["geometry_pipeline"]["shapes_computed"], 2)
            m2, c2 = build_pack(raw, pack, "2026-10-20")
            self.assertEqual(m2["counts"]["geometry_pipeline"]["shapes_reused"], 2)
            self.assertNotIn("shapes_computed", m2["counts"]["geometry_pipeline"])
            self.assertEqual(c2.get("geometry_unchanged"), 2)
            self.assertEqual((pack / "beaches.json").read_bytes(), first)
            self.assertEqual(m1["pack_hash"], m2["pack_hash"])
            self.assertFalse((pack / "cache").exists())  # the pack is the cache
            recs = {r["id"]: r for r in json.loads(first)}
            w = recs["beach_osm_w1"]
            self.assertEqual((w["osm_version"], w["osm_changeset"]), (7, 99))
            self.assertEqual(w["osm_url"], "https://www.openstreetmap.org/way/1")
            self.assertEqual(w["osm_edit_url"], "https://www.openstreetmap.org/edit?way=1")
            self.assertNotIn('"uid"', first.decode())
            self.assertEqual(recs["beach_osm_n5"]["wikipedia_url"],
                             "https://en.wikipedia.org/wiki/Hanauma_Bay")
            self.assertTrue((pack / "grids.json").exists())
            self.assertEqual(w["blocks"]["geometry"]["pulled_at"], "2026-10-20")
            self.assertIsNone(w["blocks"]["nearby"])
            self.assertEqual(m1["blocks"]["nearby"]["status"], "never run")
            self.assertEqual(w["score"], 3 + 3 + 2 + 1)  # name, wiki tag, area, lifeguard
            self.assertEqual(w["tier"], "primary")

    def test_block_not_pulled_comes_from_previous_pack_with_old_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp, "pack")
            write_raw(Path(tmp, "r1"), beach_tiles())
            build_pack(Path(tmp, "r1"), pack, "2026-10-20")
            before = {r["id"]: r for r in json.loads((pack / "beaches.json").read_text())}
            webcam = {"type": "node", "id": 77, "lat": LAT0, "lon": LNG0, "version": 3,
                      "tags": {"man_made": "surveillance", "surveillance:type": "webcam",
                               "contact:webcam": "https://example.org/cam", "name": "Cam"}}
            r2 = Path(tmp, "r2")
            write_raw(r2, [tile("HI_A", [webcam])], kind="osm_nearby", statuses=("osm_nearby",))
            man, changes = build_pack(r2, pack, "2027-01-18")
            recs = {r["id"]: r for r in json.loads((pack / "beaches.json").read_text())}
            w = recs["beach_osm_w1"]
            self.assertEqual(w["blocks"]["geometry"]["pulled_at"], "2026-10-20")
            self.assertEqual(w["blocks"]["nearby"]["pulled_at"], "2027-01-18")
            self.assertTrue(w["has_webcam"])
            self.assertEqual(changes.get("geometry_kept"), 2)
            self.assertEqual(man["blocks"]["geometry"]["last_run"], "2026-10-20")
            self.assertEqual(man["blocks"]["nearby"]["last_run"], "2027-01-18")
            carried = ("nearby_counts", "has_webcam", "has_surf_spot", "blocks", "score", "tier",
                       "seed_space")
            for rid, rec in recs.items():  # geometry fields come back unchanged
                self.assertEqual({k: v for k, v in rec.items() if k not in carried},
                                 {k: v for k, v in before[rid].items() if k not in carried})
            self.assertEqual(json.loads((pack / "grids.json").read_text())["beach_osm_w1"]
                             ["cell_count"], w["cell_count"])
            near = json.loads((pack / "nearby.json").read_text())["beach_osm_w1"]
            self.assertEqual(near[0]["webcam_url"], "https://example.org/cam")
            self.assertEqual(near[0]["distance_m"], 0.0)

    def test_link_helpers(self):
        self.assertEqual(normalize.bare_qid(" Q42 "), "Q42")
        self.assertIsNone(normalize.bare_qid("Q1;Q2"))
        self.assertEqual(normalize.wikipedia_url("haw:Waikīkī"),
                         "https://haw.wikipedia.org/wiki/Waik%C4%ABk%C4%AB")
        self.assertIsNone(normalize.wikipedia_url("Waikiki Beach"))


class BlockTests(unittest.TestCase):
    def test_due(self):
        man = {"blocks": {"nearby": {"last_run": "2026-10-20"}, "geometry": {"last_run": "2026-10-20"}}}
        self.assertFalse(blocks.is_due("nearby", man, date(2026, 12, 1)))
        self.assertTrue(blocks.is_due("nearby", man, date(2027, 1, 18)))
        self.assertTrue(blocks.is_due("wiki", man, date(2026, 10, 21)))  # never run
        self.assertTrue(blocks.is_due("geometry", None, date(2026, 10, 21)))
        self.assertEqual(blocks.choose(None, "wiki", False, None, date(2026, 10, 21)),
                         ["geometry", "nearby", "stations", "places", "land", "weather",
                          "terrain"])
        with self.assertRaises(ValueError):
            blocks.choose("weather,surf", None, False, None, date(2026, 10, 21))


class SourceTests(unittest.TestCase):
    def test_timezone_rules(self):
        z = timezone.zone
        self.assertEqual(z("HI", 21.3, -157.8), "Pacific/Honolulu")
        self.assertEqual(z("FL", 30.4, -86.5), "America/Chicago")
        self.assertEqual(z("FL", 25.8, -80.1), "America/New_York")
        self.assertEqual(z("AK", 51.9, -176.6), "America/Adak")
        self.assertEqual(z("AK", 52.9, 173.2), "America/Adak")
        self.assertEqual(z("AK", 57.1, -170.3), "America/Anchorage")  # Pribilofs
        self.assertEqual(z("MI", 45.1, -87.6), "America/Menominee")
        self.assertEqual(z("MI", 42.9, -86.2), "America/Detroit")
        self.assertEqual(z("PR", 18.4, -66.1), "America/Puerto_Rico")
        self.assertIsNone(z(None, 0, 0))

    def test_buoy_table_parse(self):
        text = ("# STATION_ID | OWNER | TTYPE | HULL | NAME | PAYLOAD | LOCATION | TZ\n"
                "51201|SI|Waverider Buoy||Waimea Bay, HI||21.671 N 158.118 W (x)|H|\n"
                "51407|NT|2.6 meter DART buoy||Hilo||19.6 N 156.5 W (x)|H|\n"
                "plxa2|NO|Water Level Observation Network||Port||60.0 N 146.0 W (x)|H|\n")
        parsed = buoys.parse(text)
        self.assertEqual([b["id"] for b in parsed], ["51201"])
        self.assertAlmostEqual(parsed[0]["lon"], -158.118)

    def test_nearest_is_exact(self):
        pts = [(21.0, -157.0, {"id": "a"}), (21.6, -158.1, {"id": "b"}), (25.0, -80.0, {"id": "c"})]
        idx = Nearest(pts)
        self.assertEqual(idx.find(21.67, -158.0)[0]["id"], "b")
        self.assertEqual(idx.find(26.0, -81.0)[0]["id"], "c")
        hit = idx.find(21.0, -157.0)
        self.assertEqual((hit[0]["id"], hit[1]), ("a", 0.0))

    def test_padus_prefers_fee_then_smallest(self):
        resp = {"features": [
            {"attributes": {"Category": "Designation", "Unit_Nm": "Big", "Pub_Access": "OA",
                            "GIS_Acres": 1}},
            {"attributes": {"Category": "Fee", "Unit_Nm": "Park B", "Pub_Access": "RA",
                            "GIS_Acres": 50, "MngTp_Desc": "State"}},
            {"attributes": {"Category": "Fee", "Unit_Nm": "Park A", "Pub_Access": "OA",
                            "GIS_Acres": 10, "MngTp_Desc": "Local", "DesTp_Desc": "Local Park"}}]}
        f = padus.fields(resp)
        self.assertEqual((f["padus_unit_name"], f["public_access"], f["land_manager_type"]),
                         ("Park A", "Open", "Local"))
        self.assertEqual(padus.fields({"features": []})["public_access"], None)
        filler = {"features": [{"attributes": {"Category": "", "Unit_Nm": "Non-PAD-US Area",
                                               "Pub_Access": "UK"}}]}
        self.assertIsNone(padus.fields(filler)["padus_unit_name"])

    def test_census_and_nws_fields(self):
        c = census.fields({"result": {"geographies": {
            "States": [{"STUSAB": "HI"}], "Counties": [{"NAME": "Honolulu County", "GEOID": "15003"}],
            "Census Designated Places": [{"NAME": "Urban Honolulu CDP", "GEOID": "1571550"}]}}})
        self.assertEqual(c, {"county": "Honolulu County", "county_fips": "15003",
                             "place": "Urban Honolulu CDP", "place_fips": "1571550",
                             "census_state": "HI"})
        n = nws.fields({"properties": {"cwa": "HFO", "gridId": "HFO", "gridX": 156, "gridY": 143,
                                       "forecastZone": "https://api.weather.gov/zones/forecast/HIZ033",
                                       "county": "https://api.weather.gov/zones/county/HIC003"}})
        self.assertEqual((n["nws_forecast_zone"], n["nws_county_zone"], n["nws_office"]),
                         ("HIZ033", "HIC003", "HFO"))
        self.assertEqual(nws.fields({"_http_status": 404})["nws_grid"], None)

    def test_padus_open_replaces_name_hint_in_score(self):
        base = {"name": "X State Park Beach", "protected_area_hint": True}
        self.assertEqual(score.score(base), 4)
        self.assertEqual(score.score({**base, "padus_unit_name": "U", "public_access": "Restricted"}), 3)
        self.assertEqual(score.score({"name": "X", "padus_unit_name": "U", "public_access": "Open"}), 4)
        park = {"name": "X", "padus_unit_name": "U", "public_access": "Open",
                "designation": "State Park"}
        self.assertEqual(score.score(park), 5)
        self.assertEqual(score.score({**park, "designation": "Military Land"}), 4)

    def test_nearby_signals_are_capped(self):
        rec = {"name": "X", "nearby_counts": {"lifeguard": 1}}
        self.assertEqual(score.score(rec), 3 + 2)
        rec["nearby_counts"] = {"lifeguard": 2, "parking": 9, "toilets": 1, "shower": 1,
                                "webcam": 1, "surf_spot": 3}
        self.assertEqual(score.nearby_points(rec), 4)
        self.assertEqual(score.score(rec), 3 + 4)
        self.assertEqual(score.tier(rec, score.score(rec)), "secondary")


class ServerAnswerTests(unittest.TestCase):
    def test_nearby_canary_rejects_silent_empty_around(self):
        from beachx_pack.http import FetchError
        from beachx_pack.nearby import check_canary
        count = lambda n: {"type": "count", "tags": {"total": str(n)}}
        check_canary({"elements": [count(13), count(13)]})
        check_canary({"elements": [count(0), count(0)]})  # a tile with no beaches
        with self.assertRaises(FetchError):
            check_canary({"elements": [count(13), count(0)]})
        with self.assertRaises(FetchError):
            check_canary({"elements": [count(97), count(2)]})  # seen live: ways dropped
        with self.assertRaises(FetchError):
            check_canary({"elements": []})

    def test_suspect_tile_drops_nothing(self):
        t = tile("HI_A", [way(1, rect(100, 50, 0), {"natural": "beach"}), marker("US", [])])
        t["suspect"] = "country area matched 0 of 1 beaches"
        geo, report = normalize.geometry_blocks([t], {})
        self.assertEqual(list(geo), ["beach_osm_w1"])
        self.assertEqual(report["country_area_unresolved"], 1)

    def test_country_area_with_no_members_is_rejected(self):
        from beachx_pack.http import FetchError
        from beachx_pack.overpass import check_areas
        el = {"type": "way", "id": 1}
        check_areas({"elements": [el, marker("US", ["way/1"])]})
        check_areas({"elements": [el, marker("US", [], found=False)]})  # area unresolved: kept
        with self.assertRaises(FetchError):
            check_areas({"elements": [el, marker("US", [])]})
        zero = {"type": "count", "tags": {"total": "0"}}
        with self.assertRaises(FetchError):
            check_areas({"elements": [el, marker("US", ["way/1"]), zero]})


class DiffTests(unittest.TestCase):
    def test_categories(self):
        def r(rid, name, lat, tier="minor", geo="g1", near="n1"):
            return {"id": rid, "name": name, "latitude": lat, "longitude": -157.0, "tier": tier,
                    "state": "HI", "blocks": {"geometry": {"hash": geo}, "nearby": {"hash": near}}}
        old = [r("a", "A", 21.0), r("b", "B", 21.0), r("c", "C", 21.0, "secondary"),
               r("d", "D", 21.0)]
        new = [r("b", "B2", 21.0, geo="g2"), r("c", "C", 21.0, "primary"),
               r("d", "D", 21.003, geo="g2", near="n2"), r("e", "E", 21.0)]
        cam = {"kind": "webcam", "osm_type": "node", "osm_id": 9, "name": "Cam",
               "osm_url": "https://www.openstreetmap.org/node/9"}
        out = diffpack.compare(old, new, {"d": []}, {"d": [cam]})
        self.assertEqual([x["id"] for x in out["added"]], ["e"])
        self.assertEqual([x["id"] for x in out["removed"]], ["a"])
        self.assertEqual([x[1]["id"] for x in out["renamed"]], ["b"])
        self.assertEqual([x[1]["id"] for x in out["moved"]], ["d"])  # about 333 m
        self.assertEqual([x[1]["id"] for x in out["retiered"]], ["c"])
        self.assertEqual(out["blocks"]["geometry"]["unchanged"], 1)
        self.assertEqual(len(out["blocks"]["nearby"]["changed"]), 1)
        text = diffpack.render(out)
        self.assertIn("webcam added at D", text)

    def test_same_hash_is_not_inspected(self):
        a = {"id": "x", "name": "A", "latitude": 21.0, "longitude": -157.0, "tier": "minor",
             "blocks": {"geometry": {"hash": "h1"}}}
        self.assertEqual(diffpack.compare([a], [dict(a, name="B")])["renamed"], [])


if __name__ == "__main__":
    unittest.main()


class EmptyTileTests(unittest.TestCase):
    def test_nearby_and_water_are_not_asked_for_a_tile_without_beaches(self):
        from unittest import mock
        from beachx_pack import overpass
        t = tiles.Tile("KS_40N_96W", 40.0, -96.0, 40.03, -94.56, "KS", "US", "inland")
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)
            overpass.write_payload(overpass.tile_path(raw, t, "osm"),
                                   {"tile": {"id": t.id}, "response": {"elements": [
                                       {"type": "beachx_area", "tags": {"code": "US"}}]}})
            with mock.patch.object(overpass, "fetch_tile") as fetch:
                status = overpass.pull_tiles([t], raw, log=lambda *_: None)
            fetch.assert_not_called()
            for kind in ("osm_nearby", "osm_water"):
                self.assertEqual(status[kind]["empty_tiles"], 1)
                self.assertNotIn("skipped", status[kind])  # a skipped status hides the block
                payload = json.loads(overpass.tile_path(raw, t, kind).read_text())
                self.assertEqual(payload["response"]["elements"], [])
                overpass.summarize(payload, kind)


class StaleServerTests(unittest.TestCase):
    def test_an_old_overpass_database_is_refused_and_the_next_endpoint_asked(self):
        from datetime import datetime, timezone
        from unittest import mock
        from beachx_pack import http
        now = datetime(2026, 10, 21, 4, 0, tzinfo=timezone.utc)
        http.check_osm_age({"osm3s": {"timestamp_osm_base": "2026-10-21T02:53:13Z"}}, now)
        http.check_osm_age({"elements": []}, now)  # not Overpass: no timestamp
        with self.assertRaises(http.StaleServer):
            http.check_osm_age({"osm3s": {"timestamp_osm_base": "2026-06-01T08:52:28Z"}}, now)

        answers = {"https://stale": {"osm3s": {"timestamp_osm_base": "2026-05-31T22:37:44Z"},
                                     "elements": []},
                   "https://fresh": {"osm3s": {"timestamp_osm_base":
                                               datetime.now(timezone.utc).strftime(
                                                   "%Y-%m-%dT%H:%M:%SZ")}, "elements": []}}
        asked = []

        class Resp:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(self.body).encode()

        def urlopen(req, timeout=None):
            asked.append(req.full_url)
            return Resp(answers[req.full_url])

        with mock.patch("urllib.request.urlopen", urlopen), mock.patch("time.sleep"):
            url, _ = http.request_json(["https://stale", "https://fresh"], data={"data": "x"},
                                       log=lambda *_: None)
        self.assertEqual(url, "https://fresh")
        self.assertEqual(asked, ["https://stale", "https://fresh"])  # the stale one only once
