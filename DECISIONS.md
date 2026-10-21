# Decisions

One entry per rule, with the reason. Add to this whenever a rule changes.

## 20 October 2026

- **Grids are official, fixed, and computed here.** A catalog beach's anchor, heading, cell size, and cell set are computed once by this pipeline. The app registers that grid as a Space on arrival and never generates cells. Reason: the grid must be the same for everyone at a beach, not whatever the first phone minted. Ad hoc stretches off the catalog keep the old phone-made grid.
- **Grids are shaped to the sand.** Cells are 5 m, run along the shore, and exist only inside the mapped outline. A grid is never a bounding square.
- **Keep every OpenStreetMap tag.** The raw tag map is stored on each beach next to the extracted fields. Reason: we do not know yet which tags the app will want.
- **Capture the keys OSM uses.** Element type, id, version, timestamp, changeset, and ready-made links to the element, its edit page, Wikidata, and Wikipedia. Reason: the app deep links to the source, and the diff can point at the changeset that moved a beach.
- **Hash by block, with a cadence per block.** Blocks: geometry, nearby, stations, places, land, weather, wiki, terrain. Each has its own hash per beach and its own re-pull interval. Reason: a webcam appearing must never invalidate a grid, and a yearly run should skip what has not changed.
- **The pack files are the cache.** A beach whose block hash matches the previous committed pack is reused from it. There is no separate cache folder. Reason: a cache folder duplicated the pack and would be hundreds of megabytes nationally.
- **Overpass runs in its own queue.** Tile fetches from OpenStreetMap run independently of every other source and can run at the same time. Reason: Overpass is slow and unreliable under load and must not block the rest.
- **PAD-US is downloaded whole.** The public land dataset is pulled once and looked up locally, not queried per point. Reason: the per-point service took up to 96 s per beach and is hosted on an account that could move.
- **EPA BEACON is dropped.** Reason: it is live advisory data, not catalog data, and there is no stable list of monitored beaches with coordinates.
- **Pull webcams, surf spots, and surf shops that OSM has.** With all their tags, attached to beaches within 300 m. Reason: the app opens a publisher's own camera page and wants to name the break.
- **Packs fold by country, then state, and ship as SQLite.** JSON per state is committed and reviewed. A per-state SQLite file, gzipped, is the phone download, with an index of size and hash per state. Reason: the phone already runs SQLite, opens the file read-only, and queries it without parsing anything up front.
- **Packs are pulled from this public GitHub repository.** Release assets, one release per pack build.
- **Spaces are seeded for primary beaches only.** Reason: seeding tens of thousands of empty spaces would clutter discovery.
- **Terrain comes from USGS 3DEP, quantised per cell.** One height per cell, rounded to 0.1 m, relative to the beach's lowest cell, baked into the grid spans, with the datum offset to the tide station and the flight date per beach. Fetched one 20 km raster per cluster at 5 m. Reason: the phone colours the tide per cell and draws slope without touching a raster. AR measurement on the sand still wins.
- **Spaces are shown on a map on the phone.** Boundaries are stored as latitude and longitude rings so they draw over a map view without conversion. Reason: people need to see where a stretch is before they are standing on it.
- **No full national pull yet.** Requirements are still arriving. Hawaii is the only pack built.
