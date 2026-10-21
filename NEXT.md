# Next, in order

The build resumed on 20 October 2026. Steps 1 to 10 are built and validated on Hawaii; the Hawaii pack was rebuilt with them. Every rule in `DECISIONS.md` applies. Keep the unit tests green, and do not run the national pull until Sky says so.

1. Done. **Two queues.** `pull --queue overpass` (geometry and nearby tiles, atomic per-tile writes) and `pull --queue sources` (everything else, from the tiles on disk). Plain `pull` runs both.
2. Done. **PAD-US as a download.** Replace per-point queries with one official download (ScienceBase state GeoPackage read with sqlite3 and a WKB parser, or a paged bulk export as fallback) and local point-in-polygon for every tier.
3. Done. **Merge overlapping OSM features.** Same sand mapped twice becomes one beach carrying every OSM id. Keep adjacent beaches separate.
4. Done. **Generated names.** "Unnamed stretch near <place>" from the places block for beaches without a name.
5. Done. **Water edge and orientation.** Find the boundary segments that meet the OSM coastline or lake polygon. Set the grid heading sign so +y points up the beach. Record `water_edge` on the boundary.
6. Done. **Terrain block.** USGS 3DEP via one 20 km raster per cluster at 5 m. Height per cell, 0.1 m steps, relative to the beach's lowest cell. Per beach: datum offset to the tide station's mean high water, flight date, wave margin from the nearest buoy's typical height.
7. Done. **Cell records.** Replace spans with the 8-byte record in `DECISIONS.md` (height, water, volatility, slope, flags, reserved) and the `rows` blob of `[y, x0, count, count × record]`. Set `cell_record_version: 1` in the manifest. Keep a JSON dump of one small beach's rows in the tests.
8. Done. **Per-state layout.** `pack/<country>/<state>/` with beaches, boundaries, grids, nearby, manifest, and `pack/index.json`. Previous-pack reuse keeps working.
9. Done. **`dist` as SQLite.** One `<state>.sqlite.gz` per state with tables beaches, boundaries, grids (anchor, heading, rows blob), nearby, manifest, and an index on latitude and longitude. `dist/index.json` with size, sha256, pack_hash, beach count, built date. `dist/` is git-ignored.
10. Done. **Overrides.** `overrides/<state>.json` keyed by beach id, applied at build: name, tier, water edge, a reason and a date. The app's admin review map will write these.
11. **Publishing.** A GitHub Action on a tag builds `dist`, pushes it to the Pages branch at `https://packs.beachx.apphaus.us/`, and attaches the files to a release.
12. **Mainland test.** Pull one mainland state and one Great Lakes state before the national run. Great Lakes beaches get `water_kind: lake` and no tide station.
13. **National pull.** Overpass queue off-peak, sources queue beside it, resumable, one to two days. Review the top 100 and a random 100 on the admin map before committing.

Not in this list, by decision: EPA BEACON, per-point PAD-US, a cache folder, zip-of-JSON packs.
