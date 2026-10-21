# BeachX.pack

The beach catalog for Beachs. It pulls every mapped beach in a US state, computes the fixed grid the app lays on each one, and packs the result per state for phones to download. The app never computes a catalog beach's grid. It is computed here, once, and registered on the phone when someone arrives.

Status, 21 October 2026: the pipeline takes one state at a time and three states are built: Hawaii, Kansas (the inland test), and California (the first mainland coast). See the table under **States built** below. The full national pull has not been run. Publishing (a GitHub Action that pushes `dist/` to Pages) is not built. See `DECISIONS.md` for every rule and why, and `NEXT.md` for what is left.

## States built

One row per state. Built 21 October 2026, pack commit d180376. Tiles are the ones that held a beach over the tiles queued. Bands are the share of cells wash / wet / dry / back.

| State | Tiles | OSM elements | Beaches | Primary / secondary / minor | Grids | Cells | Water kinds | Bands % | Download | Overpass |
|---|---|---|---|---|---|---|---|---|---|---|
| Hawaii | 4 of 4 | 472 | 463 | 83 / 136 / 244 | 441 | 524,288 | 440 sea, 2 lake | 6 / 13 / 77 / 4 | 2.3 MB | overpass-api.de |
| Kansas | 7 of 15 | 67, 2 out of state | 65 | 0 / 12 / 53 | 63 | 9,001 | 62 lake, 1 river | 4 / 6 / 90 / 0 | 0.1 MB | overpass-api.de, then .fr from tile 7 |
| California | 10 of 11 | 1,560, 128 out of state | 1,401 | 250 / 274 / 877 | 1,285 | 3,574,106 | 1,138 sea, 102 lake, 33 river | 31 / 16 / 49 / 4 | 12.3 MB | overpass-api.de, .fr for the northern 5 |

Notes per state:

- **Hawaii.** 9 merges of 18 elements, 22 node beaches with no grid. Every sea grid has a tide station; the terrain datum is the lidar's flattened sea. Waikiki is first. Primary rose from 56 to 83 once PAD-US covered every beach. Waikiki reads 99% dry. Pack JSON 8.4 MB, SQLite 7.4 MB.
- **Kansas.** The inland test: generated 2° tiles over the TIGER box, reservoirs as lakes, no tide station or buoy by rule. Bloomington Beach on Clinton Lake is first. 56 wash or wet cells sit more than 20 m from the water on flat reservoir beaches that were below pool at flight. Pack JSON 0.5 MB, SQLite 0.5 MB.
- **California.** 29 merges of 60 elements, 116 node beaches with no grid, 108 Oregon and 20 foreign elements left to their own runs. Every sea beach has its nearest NOAA station with a datum of MHW over NAVD 88, gaps filled from the nearest station within 50 km. Venice, Zuma, and Santa Monica lead. 587,171 wash cells (16%) are open sea inside oversized outlines near Crescent City and Humboldt Bay, and 244 grids rise more than 10 m where outlines take in bluffs and dunes; the surface byte is the planned answer. Pack JSON 46 MB, SQLite 38.6 MB.

## Run a state end to end

```sh
python3 -m beachx_pack pull --state HI      # both queues: Overpass, then every other source
python3 -m beachx_pack build --state HI     # raw/hi/<run-date>/ + pack/us/hi/ -> pack/us/hi/
python3 -m beachx_pack diff --state HI      # what changed against git HEAD
python3 -m beachx_pack stats --state HI     # counts, coverage, volatility, top 25
python3 -m beachx_pack dist --state HI      # pack/us/hi/ -> dist/us/hi.sqlite.gz + dist/index.json
python3 -m unittest
```

The two queues can run side by side in two shells: `pull --state HI --queue overpass` and `pull --state HI --queue sources`. The sources queue works from whatever tiles are on disk, so run it again after the Overpass queue finishes to cover beaches from late tiles. `python3 -m beachx_pack run --state HI` is pull, then build.

Then follow [Review before you commit](#review-before-you-commit). Commit `pack/` and any overrides; never commit `raw/` or `dist/`.

## How it works, start to finish

### 1. Pull, in two queues

- **The Overpass queue** walks the state's tiles and asks OpenStreetMap three questions per tile: every mapped beach with its full outline, tags, version, and timestamp; the things near beaches (webcams, surf spots, surf shops, lifeguard posts, parking, toilets, showers, drinking water); and the coastline and water polygons within 500 m of a beach. The second and third are asked by box, one half-degree cell of the tile at a time, over each beach's box widened by 300 or 500 m (`beachx_pack/subbox.py`), because `around` broke on the first mainland tile. Each answer is checked for Overpass's silent partial answers, retried when broken, and written atomically, each cell as it arrives and then one file per tile, so an interrupted pull resumes from the last good cell. A coastal state walks its hand-written coastal tiles; a state without any (Kansas) walks 2° tiles generated over its Census boundary.
- **The sources queue** fetches everything else from the tiles on disk: the NOAA tide station and buoy lists, each tide station's datums, each nearby buoy's typical wave height, Census county and place (per point), the nearest Census place, PAD-US public land as one state download looked up locally, NWS forecast zones, Wikidata and Wikipedia, and USGS 3DEP heights for every grid cell, one 20 km cluster at a time.

### 2. Build, on the phone's terms

For each beach the build computes what the phone needs and never has to compute:

- **One beach per stretch of sand.** OSM features that map the same sand twice are merged into one beach carrying every OSM id; beaches that only share an edge stay apart. Beaches outside the state (by the Census TIGER/Line state polygon) are left to that state's pack. Unnamed beaches get a display name, "Unnamed stretch near <place>".
- **The boundary.** The outline simplified to about 3 m, as rings of latitude and longitude, plus the area and the **water edge**: the stretch of the outline that meets the coastline or a lake or river.
- **The grid.** An anchor at the centroid, a heading that points +y up the beach (away from the water edge), 5 m cells shaped to the sand, and one 8-byte record per cell: height above the beach's lowest cell, distance to the water edge, volatility band (wash, wet, dry, back), slope, and build and edge flags.
- **A score and a tier**: primary, secondary, or minor, from name, wiki presence, size, public land, lifeguards, and what is nearby. Primary beaches are the ones the app seeds a Space for.
- **The enrichment.** Water kind, nearest tide station with its datums, wave margin, buoy, time zone, county and place, public access and land manager, forecast zone, terrain source and flight dates, description, deep links back to OpenStreetMap and Wikidata, and every raw OSM tag untouched.
- **Overrides.** Corrections from the admin review map in `overrides/<st>.json` (name, tier, water edge, with a reason and a date) are applied last, so they survive every re-pull.

Every block has its own hash per beach and a cadence. On a later run `pull --due` fetches only lapsed blocks, a beach whose hash has not changed is reused from the previous pack without recomputing, and `diff` lists what was added, removed, renamed, moved, or re-tiered.

### 3. Pack, folded by country then state

The committed pack is JSON per state under `pack/us/<st>/` (lower-case postal code), readable and diffable in git, with `pack/index.json` listing every state pack. `dist` turns a state into one gzipped SQLite file, `dist/us/<st>.sqlite.gz`, and lists it in `dist/index.json` with its size, sha256, pack hash, beach count, and dates. `dist/` mirrors the Pages root (`https://packs.beachx.apphaus.us/index.json`, `/us/hi.sqlite.gz`) and is git-ignored.

### 4. The phone

On launch and on refresh, the phone works out its state, reads the index, and downloads that state's file if it is missing or its sha256 changed. It keeps every state it has downloaded and opens each read-only. When someone arrives at a catalog beach, the phone registers the beach's grid as a Space, taking the anchor, heading, and cells exactly as the pack gives them. Tide colour per cell is one comparison: the cell's height plus the beach's `datum_offset_m` (metres above the tide station's mean high water) against the next predicted high tide (CO-OPS, datum MHW) plus `wave_margin_m`. AR measurement of the real sand still wins over the stored height.

### 5. A year later

`python3 -m beachx_pack run --state HI` per state. The Overpass queue re-walks the tiles, the sources queue refreshes what is due, hashes skip what did not change, `diff` shows what did. Read the top of the list and a random sample, commit the pack, build the state file, publish, and phones pick up the changed states next time they look.

## Reference

Everything below describes the pipeline as it exists today.

## Requirements

Python 3.13, standard library only. No `pip install`.

## Commands

All commands run from the repo root as `python3 -m beachx_pack <command> --state XX`, where `XX` is a USPS code (HI, KS, CA, PR, ...).

| Command | What it does |
|---|---|
| `pull --state XX [--queue overpass\|sources\|both] [--tiles ...] [--run-date YYYY-MM-DD] [--force] [--blocks a,b] [--skip-blocks a,b] [--due] [--sources a,b] [--skip-sources a,b] [--limit N] [--wikidata-limit N] [--wikipedia-limit N] [--endpoint URL]` | Fetches the chosen blocks into `raw/<st>/<run-date>/`. `--queue overpass` fetches the beach, nearby, and water tiles; `--queue sources` fetches everything else from the tiles on disk; the default runs both in that order. `--tiles` overrides the state's tiles with a region (`hawaii`, `alaska`, `atlantic`, `gulf`, `pacific`, `great_lakes`, `territories`) or a comma list of regions and USPS codes. `--due` keeps only blocks whose cadence has elapsed since the manifest's `last_run` (every block is due when there is no pack). `--limit` caps per-point calls per source (and terrain beaches). Sources: `osm`, `osm_nearby`, `osm_water`, `tides`, `buoys`, `datums`, `waves`, `census`, `timezone`, `gazetteer`, `padus`, `nws`, `wikidata`, `wikipedia`, `terrain`. |
| `build --state XX [--run-date]` | `raw/<st>/<run-date>/` (default: the latest) merged over the previous `pack/us/<st>/` and `overrides/<st>.json`, written back to `pack/us/<st>/`, and the state's entry in `pack/index.json`. |
| `run --state XX` | `pull`, then `build`. Same options as `pull`. |
| `dist --state XX` | `pack/us/<st>/` to `dist/us/<st>.sqlite.gz`, and its entry in `dist/index.json`. See [SQLite](#sqlite). |
| `diff --state XX [--against old/beaches.json]` | Compares `pack/us/<st>/` with a previous pack (default: git `HEAD`; a HEAD from before the per-state layout is read from `pack/` and filtered to the state). Per block: unchanged, changed, added, removed. For changed geometry: renamed, moved more than 250 m, with old and new `osm_version` and timestamp. Tier changes for every beach. Nearby items added or removed by name. Samples of each. |
| `stats --state XX` | The block table (last run, cadence, due, beaches covered, per-source calls), counts by tier, state, state confidence, surface, water kind, and orientation, coverage of each field, cells by volatility, the top 25 by score, and the five largest grids by cell count. |
| `show --state XX <beach id>` | One beach's record, boundary, and grid with its rows decoded, as JSON. |
| `export --state XX --dart PATH [--tier primary] [--class-name PackBeaches]` | A Dart seed class in the app's `StarterBeaches` style. Unnamed beaches are skipped. |
| `export --state XX --json PATH --tier primary\|secondary\|minor\|all [--boundaries] [--grids] [--nearby]` | A trimmed app asset (see [App JSON](#app-json)). |

## Blocks

Each beach's data is split into named blocks. A pull runs or skips whole blocks. `build` always rebuilds every record. It first reads the previous pack from disk (`pack/us/<st>/`: `beaches.json` plus `boundaries.json`, `grids.json`, and `nearby.json`). A block pulled in this run comes from `raw/<st>/<run-date>/`; every other block is carried forward from the previous pack with its old hash and `pulled_at`; a block that was never pulled is null (listed as `never run` in the manifest). The new pack is written at the end. There is no separate cache: the pack is the cache. The table lives in `beachx_pack/blocks.py`.

| Block | Cadence | Sources | Who gets it | Fields |
|---|---|---|---|---|
| `geometry` | 365 days | OSM `natural=beach`, `leisure=beach_resort`, and the coastline and water polygons within 500 m of them (Overpass) | every beach | the core fields, boundary with water edge, area, water kind, orientation, grid frame and cells; score and tier are computed from all blocks |
| `nearby` | 90 days | OSM webcams, surf spots, surf shops, lifeguard posts, parking, toilets, showers, drinking water (Overpass) | every beach | `nearby_counts`, `has_webcam`, `has_surf_spot`, `nearby.json` |
| `stations` | 365 days | NOAA CO-OPS tide station list and datums, NOAA NDBC station table and standard meteorological history | every beach | `tide_station_*`, `tide_datum_station_id`, `tide_mhw_above_msl_m`, `tide_mhw_above_navd88_m`, `tide_hat_above_mhw_m`, `tide_navd88_station_id`, `tide_hat_station_id`, `wave_buoy_*`, `wave_typical_m`, `wave_margin_m`, `wave_margin_source` |
| `places` | 730 days | US Census Geocoder (per point), built-in time zone table, Census Gazetteer places (one download) | Census: primary and secondary; time zone and nearest place: every beach | `county`, `county_fips`, `place`, `place_fips`, `census_state`, `timezone`, `nearest_place`, `nearest_place_km` |
| `land` | 365 days | USGS PAD-US 4.1 state download (Combined layer), looked up locally | every beach | `public_access`, `land_manager_type`, `land_manager_name`, `designation`, `padus_unit_name` |
| `weather` | 365 days | NWS `api.weather.gov/points` (per point) | primary and secondary | `nws_forecast_zone`, `nws_county_zone`, `nws_office`, `nws_grid` |
| `wiki` | 90 days | Wikidata (batches of 50), Wikipedia extracts (per point) | Wikidata: primary and secondary with a `wikidata` tag; Wikipedia: primary with a `wikipedia` tag | `wikidata_label`, `wikidata_sitelinks`, `wikidata_is_beach`, `wikidata_is_park`, `description`, `description_license`, `description_source_url` |
| `terrain` | 730 days | USGS 3DEP bare-earth DEM, one raster per 20 km cluster at 5 m | every beach with a grid | `terrain_source`, `terrain_project`, `terrain_flight_start`, `terrain_flight_end`, `terrain_resolution_m`, `terrain_datum`, `terrain_lowest_m`, `terrain_water_m`, and one height per cell in the grid rows |

`geometry` is the primary block: everything keys off its beach ids. Per-point blocks need tiers, so `pull` ranks the beaches first (from this run's geometry and whatever blocks are already cached) and looks up primary beaches first, then secondary, by score. The terrain pull takes only beaches whose grid has no heights yet, or whose grid changed since its heights were measured.

Every beach record carries `blocks: {"geometry": {"hash": "...", "pulled_at": "YYYY-MM-DD"}, ..., "terrain": {"hash", "pulled_at", "grid_key"}}` (null for a block it does not have). The manifest carries, per block, `pack_hash`, `last_run`, `cadence_days`, `beaches`, `status`, and per-source call counts and timings.

### Hashes

- `geometry`: SHA-256 over the OSM element's type, id, sorted tags, and geometry (node point, way points, relation members with role and points), floats fixed to 7 decimals, computed at pull time and stored on the raw element as `source_hash`. A merged beach hashes its members' hashes. When water lies within 500 m, the hash also covers each water feature's id, hash, and kind, and an override water edge. When the hash matches the previous pack (and the shape settings, recorded as `derive` in the manifest, are unchanged), the beach's area, boundary, and grid frame and cells are taken from the previous pack instead of recomputed. `build` prints how many shapes were reused versus computed.
- `nearby`: SHA-256 over the attached items' type, id, version, and sorted tags. Kept apart from geometry, so a webcam appearing or a shop closing never invalidates a grid.
- `terrain`: SHA-256 over the fields and every cell's height. The block also carries `grid_key`, a hash of the anchor, heading, and cell set the heights were measured for; heights are reused only for the same grid.
- Every other block: SHA-256 over the block's output fields for that beach.
- Each raw per-point response is stored with its own `response_hash`; each Wikidata entity with a `source_hash`.
- `manifest.json` has a `pack_hash` per block (SHA-256 over sorted `[id, hash]` pairs) and a top-level `pack_hash` over all of them, so two packs compare with one string.

### How long a run takes, and resuming

Measured on Hawaii (2026-10-20 and 21, 4 tiles, 472 elements; overpass-api.de was busy and answered partially or with 502s several times):

| Step | Calls | Time |
|---|---|---|
| Overpass: beaches, nearby, water | 4 + 4 + 4 | 264–319 s per query kind including retry waits; 35–110 s per answer |
| stations: tide list, buoy table | 1 + 1 | under 1 s each |
| stations: CO-OPS datums | 32 stations (+ reference stations) | about 15 s |
| stations: NDBC wave history | 20 buoys, 40 files | about 30 s |
| places: Census geocoder | 219 (primary and secondary) | 184 s |
| places: Gazetteer, time zone | 1 download (1.2 MB) | 1 s |
| land: PAD-US state zip | 1 download (150 MB) | 20 s, then 10 s to read and look up 463 beaches |
| weather: NWS | 219 | 179 s |
| wiki: Wikidata, Wikipedia | 2 batches, 7 | about 5 s |
| terrain: 3DEP | 441 beaches, 38 clusters, 189 requests | 3 to 15 minutes depending on the service; re-measuring from kept rasters takes 70 s |
| build | | 3 s reusing shapes, 32 s recomputing them |
| dist | | 1 s |

A first run of a state is dominated by Overpass and 3DEP. For a big state (California) expect hours of Overpass and an hour or more of 3DEP; both resume.

Every pull is resumable. Tile responses go to `raw/<st>/<run-date>/osm/<tile>.json`, `osm_nearby/<tile>.json`, and `osm_water/<tile>.json`, per-point responses to `raw/<st>/<run-date>/<source>/<beach id>.json`, heights to `terrain/<beach id>.json` with the raster pieces in `terrain/rasters/`, each written atomically as soon as it arrives. Anything already there for that run date is never fetched again unless you pass `--force`. If a pull dies or a tile fails after its retries, run the same command again with the same `--run-date`. `run` refuses to build until every selected tile is present. A source that is unreachable (three failures in a row, or a failed download) is skipped with a note in `raw/<st>/<run-date>/_status/<source>.json` and in the manifest; it never stops the pull.

## Outputs

`raw/` and `dist/` are git-ignored (except `raw/README.md`). `pack/` and `overrides/` are committed.

| Path | What |
|---|---|
| `pack/index.json` | One entry per state pack: path, beaches, primary, grid cells, pack hash, run date, cell record version. |
| `pack/us/<st>/beaches.json` | One record per beach, sorted by name, then id. A JSON array with one record per line, so git diffs stay readable. |
| `pack/us/<st>/beaches.csv` | The same records without `tags`, `blocks`, `nearby_counts`, `merged_from`, and `nws_grid` (bbox split into four columns, lists joined with `; `). |
| `pack/us/<st>/boundaries.json` | Simplified boundaries with their water edges, keyed by beach id, one beach per line. |
| `pack/us/<st>/grids.json` | Official grids keyed by beach id, one beach per line, the cells as a base64 rows blob. |
| `pack/us/<st>/nearby.json` | Nearby items keyed by beach id. |
| `pack/us/<st>/manifest.json` | Country, state, run date, pipeline version, `cell_record_version`, `pack_hash`, sources with URLs, licenses and attribution, the block table, tile ids, counts (including cells by volatility and overrides applied). No wall-clock time, so rebuilding the same inputs gives identical files. |
| `overrides/<st>.json` | Review corrections. See [Overrides](#overrides). |
| `dist/index.json`, `dist/us/<st>.sqlite.gz` | The phone downloads. See [SQLite](#sqlite). |

Size: the Hawaii pack (463 beaches) is 8.4 MB of JSON (`grids.json` 5.8 MB, `beaches.json` 1.6 MB, `nearby.json` 0.6 MB, `boundaries.json` 0.4 MB, `beaches.csv` 0.35 MB) and 2.3 MB as gzipped SQLite. The kept 3DEP raster pieces in `raw/` are about 280 MB.

## Sources

| Source | Block | Endpoint | License | Attribution |
|---|---|---|---|---|
| OpenStreetMap via Overpass | geometry, nearby | `https://overpass-api.de/api/interpreter`, falling back to `https://overpass.openstreetmap.fr/api/interpreter`, then `https://overpass.kumi.systems/api/interpreter` (each refused when its data is more than three days old) | ODbL 1.0 | "© OpenStreetMap contributors" |
| NOAA CO-OPS tide prediction stations | stations | `https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json?type=tidepredictions` | US public domain | NOAA CO-OPS |
| NOAA CO-OPS tidal datums | stations | `https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/<id>/datums.json?units=metric` | US public domain | NOAA CO-OPS |
| NOAA NDBC station table | stations | `https://www.ndbc.noaa.gov/data/stations/station_table.txt` | US public domain | NOAA NDBC |
| NOAA NDBC standard meteorological data | stations | `https://www.ndbc.noaa.gov/data/historical/stdmet/<id>h<year>.txt.gz`, then `realtime2/<ID>.txt` | US public domain | NOAA NDBC |
| US Census Geocoder | places | `https://geocoding.geo.census.gov/geocoder/geographies/coordinates` (`benchmark=Public_AR_Current`, `vintage=Current_Current`) | US public domain | US Census Bureau |
| US Census Gazetteer, places (2025) | places | `https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_place_national.zip` | US public domain | US Census Bureau |
| Time zone table | places | built in (`sources/timezone.py`) | n/a | IANA zone names |
| USGS PAD-US 4.1 state downloads | land | ScienceBase item `6759abcfd34edfeb8710a004`, `PADUS4_1_State_<ST>_GDB_KMZ.zip`; fallback: the PAD-US Public Access feature service, paged | US public domain | USGS Gap Analysis Project |
| NWS API | weather | `https://api.weather.gov/points/<lat>,<lng>` | US public domain | NOAA National Weather Service |
| Wikidata | wiki | `https://www.wikidata.org/w/api.php` (`wbgetentities`) | CC0 1.0 | Wikidata |
| Wikipedia | wiki | `https://<lang>.wikipedia.org/w/api.php` (`prop=extracts`, `exintro`, `explaintext`) | CC BY-SA 4.0 | Wikipedia contributors; each `description` carries `description_source_url` |
| USGS 3DEP bare-earth DEM | terrain | `https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer` (`exportImage`, `query`) | US public domain | U.S. Geological Survey, 3D Elevation Program |
| US Census TIGER/Line states (2024) | state assignment | `https://www2.census.gov/geo/tiger/TIGER2024/STATE/tl_2024_us_state.zip` | US public domain | US Census Bureau |

Nominatim is not used. Every request sends `User-Agent: BeachX.pack catalog, hello@apphaus.us`. Overpass gets one query at a time, a 5 s pause between queries, and retries with doubling backoff (20 s, 40 s, 80 s) on 429, 5xx, timeouts, Overpass "runtime error" remarks, and answers that fail their check query, then the fallback endpoint. An Overpass answer from a database more than three days old (`timestamp_osm_base`) is refused and the next endpoint asked at once. A server with no area database (overpass.openstreetmap.fr) gets the beach query again without the area checks, and its answer must match its own count of the beaches. Per-point sources run one request at a time with a short pause (Census and NWS 0.5 s, Wikipedia 0.5 s, CO-OPS 0.3 s, NDBC 0.5 s) and 3 tries with backoff. 3DEP gets one request at a time, a 1 s pause between clusters, and 5 tries with doubling backoff from 20 s. Wikidata sends `maxlag=5`. Whole files (PAD-US, TIGER, the Gazetteer) are downloaded once into `raw/downloads/` and kept.

Wikipedia text is CC BY-SA 4.0, which is share-alike. It sits in `description` with its license and source URL on every record; keep that attribution wherever the app shows it.

### Dropped

EPA BEACON (swimming advisories) was dropped on 20 October 2026. It is live advisory data, not catalog data, and EPA publishes no stable list of monitored beaches with coordinates to match against. The app can show advisories later as a live pull if a source appears. The per-point PAD-US feature service query was replaced by the state download.

### Tiles

A coastal state walks its hand-written tiles in `beachx_pack/tiles.py`, drawn from each state's coastal extent: roughly 2° by 2°, and 2° by 4° in Alaska (at 51–72° N that is about the same ground size); the Aleutians are split at the 180° meridian and the Northwestern Hawaiian Islands are not covered. There are 128 hand-written tiles: Hawaii 4, Atlantic 20, Gulf 13, Pacific 16, Great Lakes 20, Alaska 52, territories 3. A state with none of its own (an inland state such as Kansas) walks 2° tiles on even degrees over its TIGER/Line box, region `inland`, each clipped to the box grown by 0.03° (a little over the 2 km rule), so Kansas's 350 m north of 40° N is a thin strip, not a row of Nebraska. Tiles overlap in places; elements are deduplicated by OSM type and id, and beaches outside the state are left to that state's run.

A 2° coastal tile reaches well inland, so lake and river beaches inside a coastal tile are included; the coastal states' inland lakes beyond their tiles are not covered yet.

Tiles also reach into Canada, Mexico, the Bahamas, and the British Virgin Islands. Each tile query still asks Overpass which of its beaches lie inside the US country area and each nearby state area; that answer guards against Overpass's silent partial answers. The state a beach belongs to comes from TIGER/Line (below).

## Fields

Every field comes from a source or from a rule written here. Unknown is `null`, never a guess.

### geometry

| Field | Source or rule |
|---|---|
| `id` | `beach_osm_w<id>` for ways, `beach_osm_r<id>` for relations, `beach_osm_n<id>` for nodes; for a merged beach, its primary member's. Stable across pulls. |
| `osm_type`, `osm_id` | OSM element (the primary member of a merged beach) |
| `osm_ids` | every member, as `type/id`, primary first |
| `merged_from` | the other members of a merged beach: type, id, URL, version, timestamp, changeset, name, and every tag; `[]` otherwise |
| `osm_url`, `osm_edit_url` | `https://www.openstreetmap.org/<type>/<id>`, `https://www.openstreetmap.org/edit?<type>=<id>` |
| `osm_version`, `osm_timestamp`, `osm_changeset` | OSM metadata (`out meta`). The editor's user name and uid stay in `raw/` only. |
| `name` | `name` tag (on a merged beach, the first member that has one); an override can correct it |
| `display_name` | `name`, else "Unnamed stretch near <nearest_place>", else "Unnamed stretch" |
| `alt_names` | `alt_name` (split on `;`), `official_name`, `name:en`, and every other member's names, minus duplicates of `name`; `null` when empty |
| `latitude`, `longitude` | Nodes: the node. Ways and relations: the centre of the bounding box (of every member, for a merged beach). |
| `bbox` | `[south, west, north, east]` from Overpass bounds; `null` for nodes |
| `area_m2` | Shoelace area of the unsimplified outer ring(s) minus inner rings, in the local frame below, around `latitude`/`longitude`; for a merged beach, the union, estimated on a lattice. `null` for nodes, open ways, and relations whose rings do not close. |
| `state`, `state_confidence`, `country` | See [State](#state). |
| `surface`, `access`, `fee`, `dog`, `wheelchair`, `operator` | the tag of the same name (gaps filled from other members) |
| `lifeguard` | `lifeguard` tag, else `supervised` |
| `nudist` | `nudism` tag |
| `website` | `website` tag, else `contact:website` |
| `wikipedia` | the tag, as is |
| `wikipedia_url` | `<lang>:<title>` becomes `https://<lang>.wikipedia.org/wiki/<title>` with spaces as underscores, percent-encoded; `null` without a language prefix |
| `wikidata`, `wikidata_url` | the tag when it is exactly one QID (else `null`; the raw value stays in `tags`), and `https://www.wikidata.org/wiki/<QID>` |
| `is_resort` | `leisure=beach_resort` on any member |
| `protected_area_hint` | `name` or `operator` contains (case-insensitive) one of: state park, state beach, state recreation area, state seashore, national park, national seashore, national lakeshore, national recreation area, national wildlife refuge, wildlife refuge, national monument, county park, regional park, recreation area, beach park, nature preserve, preserve, reserve, wildlife area, park service, parks and recreation, department of parks, department of natural resources, fish and wildlife; or the element is tagged `boundary=protected_area`, `boundary=national_park`, `leisure=nature_reserve`, or `leisure=park`. (`PROTECTED_WORDS` in `normalize.py`.) |
| `osm_tags_count` | number of OSM tags |
| `has_boundary`, `boundary_points`, `has_grid`, `cell_count` | see [Boundaries](#boundaries) and [Grids](#grids) |
| `water_kind` | `sea`, `lake`, or `river`: the kind of water the water edge meets (longest stretch), else of the nearest water within 500 m, else `null`. See [Water](#water). |
| `water_edge_m` | length of the water edge, metres |
| `orientation` | how the grid's heading was found: `water`, `nearby_water`, `ambiguous_nearby`, `override`, or `outline` |
| `score`, `tier`, `seed_space` | see [Tiers](#tiers) |
| `override` | `null`, or what an override changed: `fields`, `reason`, `date`, and `osm_name` when the name was corrected |
| `tiles` | ids of the tiles the element came from (a beach whose tiles are re-pulled and that is no longer there is removed) |
| `last_pulled` | the geometry block's `pulled_at` |
| `tags` | the primary element's full raw OSM tag map, unchanged |

### Other blocks

| Field | Source or rule |
|---|---|
| `nearby_counts` | items within 300 m, by kind (only kinds with at least one) |
| `has_webcam`, `has_surf_spot` | at least one item of that kind |
| `tide_station_id`, `tide_station_name`, `tide_station_distance_km` | nearest NOAA CO-OPS tide prediction station (great-circle distance from `latitude`/`longitude`); `null` on a lake or river, and on a beach with no water within 500 m when the station is more than 100 km away |
| `tide_mhw_above_msl_m`, `tide_mhw_above_navd88_m`, `tide_hat_above_mhw_m` | mean high water above mean sea level and above NAVD 88, and the highest astronomical tide above mean high water, metres. Each comes from the tide station, or its reference station when it is subordinate; a difference neither publishes comes from the nearest station within 50 km that does (at most 10 asked); `null` when none does |
| `tide_datum_station_id`, `tide_navd88_station_id`, `tide_hat_station_id` | the station that gave mean sea level, NAVD 88, and HAT: the tide station, its reference station, or the neighbour that filled the gap |
| `wave_buoy_id`, `wave_buoy_name`, `wave_buoy_distance_km` | nearest NDBC station whose type contains "buoy", excluding DART, drifting, Atlas, and tsunami types. It may not report waves. `null` on a lake or river outside the Great Lakes, and on a beach with no water within 500 m when the buoy is more than 100 km away. |
| `wave_typical_m`, `wave_margin_m`, `wave_margin_source` | median WVHT over the last full year at the nearest buoy within 100 km that reports waves; the margin is half of it, 0.2 to 2.0 m; without one, 0.5 m on the sea and 0.3 m on a lake or river (`default`) |
| `county`, `county_fips` | Census geocoder `Counties` NAME and GEOID (5 digits) |
| `place`, `place_fips` | Census `Incorporated Places`, else `Census Designated Places`, NAME and GEOID (7 digits) |
| `census_state` | Census `States` STUSAB (kept as a field; it no longer changes `state`) |
| `timezone` | IANA zone from the built-in table. Splits: Florida west of -85.0 is America/Chicago (the Apalachicola River approximated by -85.0; Port St. Joe, Cape San Blas, and the St. Joseph Peninsula are, as far as we know, Eastern but fall west of it); Alaska west of -169.5 and south of 55° N, or east of 180°, is America/Adak (the Pribilofs and St. Lawrence Island stay America/Anchorage); Michigan's Menominee County shore (south of 46° N, west of -87.35) and Gogebic County shore (west of -89.36) are America/Menominee; Indiana's Lake Michigan shore (west of -86.52) is America/Chicago; Kansas west of -101.45 between 37.73 and 39.57° N and Texas west of -104.9 are America/Denver. Hawaii Pacific/Honolulu, PR America/Puerto_Rico, VI America/St_Thomas. |
| `nearest_place`, `nearest_place_km` | nearest Census incorporated place or CDP (Gazetteer internal point, LSAD suffix dropped) within 30 km |
| `public_access`, `land_manager_type`, `land_manager_name`, `designation`, `padus_unit_name` | PAD-US `Pub_Access` (OA, RA, XA, UK become Open, Restricted, Closed, Unknown), manager type and name and designation type (decoded from the state geodatabase's coded-value domains), and unit name, for Combined-layer units within 30 m of the lookup point. When several units match: Fee before Easement before Designation before Proclamation, then the smallest `GIS_Acres`. All null when no unit matches. |
| `nws_forecast_zone`, `nws_county_zone`, `nws_office`, `nws_grid` | NWS points: last path segment of `forecastZone` and `county`, `cwa`, and `{gridId, gridX, gridY}`. A 404 (no forecast at that point) gives nulls. |
| `wikidata_label`, `wikidata_sitelinks` | English label, number of sitelinks |
| `wikidata_is_beach` | `P31` includes beach (Q40080) or state beach (Q35800652) |
| `wikidata_is_park` | `P31` includes park, urban park, protected area, national park, nature reserve, state park, county park, regional park, National Seashore, National Recreation Area, state recreation area, National Wildlife Refuge, or marine protected area (QIDs in `sources/wikidata.py`) |
| `description`, `description_license`, `description_source_url` | first paragraph of the article's plain-text intro, "CC BY-SA 4.0", and the article URL (after redirects) |
| `terrain_source`, `terrain_project`, `terrain_flight_start`, `terrain_flight_end`, `terrain_resolution_m` | "USGS 3DEP", and the finest 3DEP catalog dataset under the lookup point: its name, flight start and end dates, and ground resolution |
| `terrain_datum` | `NAVD88`, or `LMSL` (local mean sea level) in Hawaii, Puerto Rico, the Virgin Islands, and the Pacific territories |
| `terrain_lowest_m` | height of the beach's lowest cell in `terrain_datum`; cell heights are 0.1 m steps above it |
| `terrain_water_m` | median height 15 m seaward of the water-edge vertices: the hydro-flattened water surface at the time of the flight |
| `datum_offset_m` | `terrain_lowest_m` minus mean high water in the same datum (the water surface on a lake or river): a cell is `height / 10 + datum_offset_m` metres above mean high water |
| `wet_limit_m` | metres above mean high water that count as wet: `tide_hat_above_mhw_m` plus `wave_margin_m` (the margin alone on a lake or river) |
| `tide_reference` | what mean high water was measured from: `MHW over NAVD88`, `MHW over MSL`, `MHW over the flattened sea`, `water surface at flight`, or `lowest cell`. NAVD 88 terrain without a NAVD 88 tie is never read against mean sea level: the offset, wet limit, and bands stay unknown |

Per-point lookups use a point inside the beach polygon (the middle of the widest inside span on a latitude line through the polygon), not the bounding-box centre, which can sit offshore. Nodes and open ways use `latitude`/`longitude`.

### State

A beach belongs to the state whose Census TIGER/Line 2024 polygon holds its lookup point (`state_confidence` `tiger`). TIGER/Line state polygons include coastal and territorial water out to three nautical miles, so a beach on the shore is inside its state. A beach in no state but within 2 km of the state being built belongs to it (`tiger_near`). Every other beach in the state's tiles is left to its own state's run and counted as `other_state` in the manifest. `country` is `US`, or `PR` / `VI` for those packs.

## Merging

Same sand mapped twice is one beach (`beachx_pack/merge.py`): two polygons when at least half of the smaller lies inside the larger (estimated on a lattice of about 400 points); a node, or an open way's middle vertex, inside a polygon; two same-named pins within 100 m. Members merge only when their names agree or one is unnamed, and an unnamed outline that touches two or more different names merges with none of them, so a long unnamed stretch with several named beach pins keeps every beach. The primary member (polygons first, then the largest, then named, then way before relation before node, then the lowest id) gives the beach its id, tags, and fields; gaps are filled from the others, and every member is listed in `osm_ids` and `merged_from`. Hawaii: 472 elements become 463 beaches (9 merges of 18 elements).

## Boundaries

For each way and multipolygon relation:

1. Build the outer ring(s) from `out geom` coordinates as `[lat, lng]` pairs. Relation members with role `inner` become holes; all other way members are outer. Pieces are joined end to end (reversing when needed). Pieces that never close are dropped.
2. Close each ring and drop duplicate consecutive points. A ring needs 3 distinct points.
3. Simplify with Douglas–Peucker in local metres at a 3 m tolerance. A closed ring is split at its first point and the point farthest from it, and each half is simplified. Coordinates are rounded to 7 decimals.

An open way (a beach drawn as a line) gets no boundary and no area; it is not closed artificially. Nodes have no boundary.

```json
{"beach_osm_w123": {"rings": [[[21.27, -157.82], ...]], "point_count": 42, "simplified_from": 310,
  "holes": [[[...]]], "parts": [{"rings": [0], "holes": []}, ...],
  "water_edge": [[[21.27, -157.82], [21.271, -157.821], ...]]}}
```

`holes` appears only when the beach has inner rings. `parts` appears only on a merged beach: which rings and holes belong to which member (rings may overlap; a point is on the beach when it is inside any member). `water_edge` is the polylines of the outline that meet the water (see [Water](#water)), `[]` when none does. Raw, unsimplified geometry stays in `raw/`.

The local frame uses the app's constants: `east = (lng - lng0) * 111320 * cos(lat0)`, `north = (lat - lat0) * 110540`.

## Water

A third Overpass query per tile returns every `natural=coastline` way and every water polygon (`natural=water`, `waterway=riverbank`, and the older `landuse=reservoir`) within 500 m of a beach, with full geometry (`beachx_pack/water.py`, `beachx_pack/shore.py`). It is asked by box: every water feature crossing a beach's box widened by 500 m, a superset the build cuts back to the features whose box comes within 500 m of the beach's box (see `beachx_pack/subbox.py`).

- **Kinds.** `sea`: the coastline, water tagged `water=lagoon|harbour|bay|cove|tidal_pool`, `salt=yes`, or `tidal=yes`, and any water polygon whose outline comes within 10 m of the coastline (it opens to the sea). `river`: `waterway=riverbank` and river-like `water=*`. `lake`: other water, including `landuse=reservoir`. Great Lakes coastline is `lake`. A lake or river beach has no tide station.
- **Which side is water** comes from the water itself: the OSM coastline keeps land on its left, and a water polygon holds its water inside. Many OSM beaches are drawn seaward of the coastline (between high and low water), so the outline's own shape never decides it.
- **Water edge.** An outline segment is water edge when at least half of its sample points (every 2.5 m) lie within 10 m of a coastline or water outline, or in the water.
- **Orientation.** The water-side normals of the coastline and water outlines within 60 m of the beach, weighted by length, point to the water; the grid's +y is the opposite direction. Without water that close, or when the normals cancel, the direction from the beach's centre to the nearest water within 500 m stands in.
- **Water distance.** From each cell centre to the nearest coastline or water outline; 0 in the water or within one cell (5 m) of its edge.

## Grids

Grids are official, fixed, and computed here. The app registers a beach's grid as a Space on arrival and never generates cells. It takes the anchor, heading, cell size, and cell set exactly as given, and then only maps its own GPS and AR pose into that fixed frame to know which cell it is standing on and where to draw. A grid is shaped to the sand; it is never a bounding square.

For every beach with a boundary:

- `cell_size_m` = 5.
- `anchor` = the area-weighted centroid of the simplified outer ring(s), rounded to 7 decimals.
- `heading_degrees` = +y up the beach: the direction away from the water (see [Water](#water)), degrees clockwise from true north in [0, 360), rounded to 0.1°. +x runs along the shore, so a row is a band parallel to the water. With no water at all, +y is across the outline's long axis (its first principal axis plus 90°, folded into [0, 180)).
- Frame, with `h` the heading in radians and east/north from the anchor as above: `x = east*cos(h) - north*sin(h)`, `y = east*sin(h) + north*cos(h)`; inverse `east = x*cos(h) + y*sin(h)`, `north = -x*sin(h) + y*cos(h)`.
- A cell `(cx, cy) = (floor(x/5), floor(y/5))` is in the grid when its centre `(cx*5 + 2.5, cy*5 + 2.5)` lies inside an outer ring and outside every hole (ray casting, even-odd); on a merged beach, inside any member.
- The computation uses the rounded anchor, the rounded heading, and the rounded simplified rings, so the stored numbers reproduce the cell set exactly.

```json
{"beach_osm_w123": {"anchor": {"latitude": 21.2767, "longitude": -157.8278, "heading_degrees": 210.0},
  "cell_size_m": 5, "cell_count": 320, "x_min": -20, "x_max": 19, "y_min": -4, "y_max": 3,
  "cell_record_version": 1, "rows": "<base64>"}}
```

### Cell records

`rows` is the base64 of a little-endian blob: `[y int16, x0 int16, count uint16, count × record]` repeated, rows in ascending y, runs in ascending x0 (a row split by a hole has two runs). Each record is 8 bytes (`beachx_pack/cells.py`):

| Byte | Field | Meaning |
|---|---|---|
| 0–1 | `height` int16 | 0.1 m above the beach's lowest cell; -32768 unknown |
| 2–3 | `water` int16 | 0.1 m from the cell centre to the water; 0 in the water or within 5 m of it; clamped at 32767 (also: no water within 500 m) |
| 4 | `volatility` uint8 | 0 wash (below mean high water), 1 wet (up to `wet_limit_m` above it), 2 dry (above), 3 back (a dry cell rising at least 20° from the cell on its water side, and every dry cell above it in the same column: dune face, wall, cliff), 255 unknown (no terrain or no tidal datums) |
| 5 | `slope` uint8 | whole degrees to the steepest of the eight neighbours, 255 unknown |
| 6 | `flags` uint8 | bit 0 build (wet or dry, beach `surface` absent or `sand`, slope at most 10°, not edge), bit 1 edge (the boundary crosses the cell's square) |
| 7 | reserved | 0 |

A cell is `height / 10 + datum_offset_m` metres above mean high water, so the phone colours the tide per cell by comparing that with the next predicted high tide (CO-OPS, `datum=MHW`) plus `wave_margin_m`. `python3 -m beachx_pack show --state HI <beach id>` prints a beach's rows decoded; `tests/rows_fixture.json` keeps one small beach's rows as JSON beside its blob. No beach is skipped for size.

## Nearby

A second Overpass query per tile pulls, with `out meta center`:

| Kind | OSM |
|---|---|
| `webcam` | `man_made=surveillance` + `surveillance:type=webcam`, or any element with `contact:webcam`, `webcam`, or `url:webcam` |
| `lifeguard` | `emergency=lifeguard` |
| `surf_shop` | `shop=surf`, `amenity=surf_school`, or `sport=surfing` on something with a `shop` or `club` tag |
| `surf_spot` | other `sport=surfing`, or a named `natural=reef` |
| `parking`, `toilets`, `shower`, `drinking_water` | `amenity=` the same |

An item gets the first kind that matches in that order. Overpass returns the items inside each beach's box widened by 300 m (asked by box, see `beachx_pack/subbox.py`); the build then measures the exact distance in local metres from the item's point to the nearest boundary edge (0 inside the polygon), or, for nodes and open ways (which have no polygon), to the beach point, and attaches the item to every beach within 300 m.

`pack/us/<st>/nearby.json`, keyed by beach id, sorted by kind, distance, and id:

```json
{"kind": "webcam", "osm_type": "node", "osm_id": 1, "osm_url": "https://www.openstreetmap.org/node/1", "osm_version": 3,
 "name": "...", "latitude": 21.27, "longitude": -157.82, "distance_m": 41.5, "website": null,
 "webcam_url": "https://...", "tags": {...}}
```

`webcam_url` is the first of `contact:webcam`, `webcam`, `url:webcam` that starts with `http://` or `https://`. OSM webcam URLs are often the publisher's own page, which is what the app wants: it opens the publisher page and never embeds a stream it does not own.

## Tiers

Every number lives in the `SCORE` table in `beachx_pack/score.py`.

| Condition | Points |
|---|---|
| has a name | +3 |
| has a `wikipedia` or `wikidata` tag | +3 |
| `wikidata_sitelinks` ≥ 5 | +2 |
| `wikidata_sitelinks` ≥ 20 | +2 more |
| `area_m2` ≥ 50,000 | +2 |
| `area_m2` ≥ 200,000 | +1 more |
| protected: PAD-US found a unit and its public access is Open; when PAD-US has no unit, `protected_area_hint` | +1 |
| PAD-US unit whose designation contains "Preserve", "Park", "Seashore", or "Recreation" (any access) | +1 more |
| `lifeguard` tag = `yes` (not `seasonal`) | +1 |
| `is_resort` | +1 |
| within 300 m (nearby block): a lifeguard post +2, parking +1, toilets +1, a shower +1, a webcam +1 | capped at +4 for the group |

A name containing "beach", "shore", or "strand" gets no bonus and no penalty.

`primary` is score ≥ 8, `secondary` is 4 to 7, `minor` is below 4. Unnamed beaches are always `minor`. `seed_space` is `true` for primary beaches: the list the app creates named Spaces for.

The nearby and PAD-US signals let a well-known beach with thin OSM tags rise: in Hawaii, Hanauma Beach (no wiki tags, 14,000 m²) gets +4 from its lifeguard post, parking, and toilets.

PAD-US is looked up for every beach, so every named beach on open public land gets its protection points. That moved Hawaii from 56 to 83 primary beaches on 20 October 2026; Kailua, Big Beach, Hanauma, and Kamaole II are among those that rose.

The per-point blocks pick their beaches by tier before they run, so the tier used for that choice comes from the data available at that moment (for the first run, without PAD-US and Wikidata sitelinks). A named beach with a `wikidata` tag scores at least 6, so it is never left out of the Wikidata lookup.

## App JSON

`export --state XX --json PATH --tier T` writes `{"run_date", "attribution", "count", "beaches": [...]}` with, per beach: `id, name, latitude, longitude, state, tier, surface, access, lifeguard, seed_space, osm_type, osm_id, osm_url, osm_version, osm_timestamp, wikidata, wikipedia_url, timezone, tide_station_id, wave_buoy_id, county, place, public_access, land_manager_type, designation, nws_forecast_zone, nws_grid, description, display_name, water_kind, datum_offset_m, wet_limit_m, wave_margin_m`. `--boundaries`, `--grids`, and `--nearby` add `boundary`, `grid`, and `nearby` for the selected tier.

## Dart export

`python3 -m beachx_pack export --state HI --dart raw/PackBeaches.dart --tier primary` writes the app's seed shape. The app side wires it in later, next to `lib/src/catalog/starter_beaches.dart`:

```dart
// GENERATED by BeachX.pack (python3 -m beachx_pack export --dart). Do not edit.
// Data © OpenStreetMap contributors, ODbL-1.0. Pack run 2026-10-20.
import '../core/device_stamp.dart';
import '../models/beach.dart';

/// Catalog beaches from BeachX.pack. Missing rows are inserted. Existing rows stay.
abstract final class PackBeaches {
  static final stamp = DeviceStamp(
    deviceComputedAt: DateTime.utc(2026, 10, 20),
    deviceId: 'catalog',
    revision: 1,
    localOnly: false,
  );

  static List<Beach> beaches() {
    Beach beach(String id, String name, double latitude, double longitude) {
      return Beach(
        id: id,
        name: name,
        latitude: latitude,
        longitude: longitude,
        official: true,
        stamp: stamp,
      );
    }

    return [
      beach('beach_osm_w123', 'Waikiki Beach', 21.27670, -157.82780),
    ];
  }
}
```

## SQLite

`python3 -m beachx_pack dist --state HI` writes `dist/us/hi.sqlite.gz` (`beachx_pack/dist.py`), the file the phone downloads, unzips once, and opens read-only:

| Table | Columns |
|---|---|
| `manifest` | one row: country, state, run date, pack hash, `cell_record_version`, `schema_version`, attribution, and the whole manifest as `json` |
| `beaches` | `id` (primary key), `name`, `display_name`, `latitude`, `longitude`, `state`, `country`, `tier`, `score`, `seed_space`, `surface`, `access`, `lifeguard`, `water_kind`, `orientation`, `area_m2`, `cell_count`, `timezone`, `tide_station_id`, `wave_buoy_id`, `datum_offset_m`, `wet_limit_m`, `wave_margin_m`, `osm_type`, `osm_id`, `osm_url`, `wikidata`, `wikipedia_url`, `description`, `description_source_url`, and the whole record as `json`; index `beaches_latitude_longitude` |
| `boundaries` | `beach_id`, `rings`, `holes`, `parts`, `water_edge` (JSON) |
| `grids` | `beach_id`, `anchor_latitude`, `anchor_longitude`, `heading_degrees`, `cell_size_m`, `cell_count`, `x_min`, `x_max`, `y_min`, `y_max`, `cell_record_version`, `rows` (the blob, as bytes) |
| `nearby` | `beach_id`, `kind`, `osm_type`, `osm_id`, `name`, `latitude`, `longitude`, `distance_m`, `website`, `webcam_url`, `tags` (JSON); index on `beach_id` |

`dist/index.json` lists every state file with its path, gzipped and SQLite sizes, sha256, pack hash, beach, grid, and cell counts, `cell_record_version`, `schema_version`, run date, and built date. The same pack gives the same bytes, so the sha256 changes only with the pack. `dist/` mirrors the Pages root and is git-ignored.

## Overrides

`overrides/<st>.json` holds corrections from the admin review map, keyed by beach id, applied at build (`beachx_pack/overrides.py`):

```json
{"beach_osm_w123": {"name": "Queen's Beach", "tier": "primary",
                    "water_edge": [[[21.27, -157.82], [21.27, -157.81]]],
                    "reason": "two beaches mapped as one", "date": "2026-10-20"}}
```

`reason` and `date` are required; `name`, `tier`, and `water_edge` are each optional, and the file is validated on load. A name is applied before scoring and the OSM name is kept in `override.osm_name`; a tier is applied after scoring and wins; a water edge replaces the detected one, turns +y away from it, and is what water distance is measured to. Ids that match no beach are listed in the manifest's `counts.overrides.unmatched` and printed by `build`. `overrides/hi.json` ships empty.

## Review before you commit

1. `python3 -m beachx_pack diff --state HI`. Large `removed` or `moved` counts usually mean a tile failed or a mapper restructured a beach. Open the changesets it prints.
2. `python3 -m beachx_pack stats --state HI`. Check the block table (every block you pulled shows today's date, nothing unexpected is `never run`, source notes make sense), the tier counts against the last manifest, water kinds and orientations, terrain and datum coverage, the volatility split, and `other_state` in the manifest.
3. Look at the top 25 (from `stats`) and a random 25:
   `python3 -c "import json,random; b=json.load(open('pack/us/hi/beaches.json')); [print(r['tier'], r['display_name'], r['osm_url']) for r in random.sample(b, 25)]"`.
4. Spot-check a few grids with `show`: +y should point away from the water (the water-0 cells in the lowest rows), heights should be plausible, and the volatility bands should run wash, wet, dry, back away from the water.
5. `python3 -m unittest`.
6. Commit `pack/` and any overrides and code changes. Do not commit `raw/` or `dist/`. The committed pack is also what the next build reuses.

## Tests

`python3 -m unittest` runs offline on fixture snippets: tiles (hand-written and generated), state polygons and the shapefile reader, ids and links, score and tier thresholds, the ring builder, the simplifier, grids (heading across the long axis without water, holes, merged unions), merging (same sand, adjacent beaches, names that disagree, an unnamed stretch with several named pins), the water edge and orientation (a beach on the coastline, a beach drawn seaward of it, a lake, no water), the cell record and rows blob (byte layout, round trip, and `tests/rows_fixture.json`), volatility bands, back cells, slope and flags, the GeoTIFF reader (plain, deflate, LZW), PAD-US KML and lookup, the Gazetteer and generated names, a second build that reuses every shape and writes a byte-identical `beaches.json`, a block carried forward with its old date, block cadence and `--due`, time zones, the NDBC parser, nearest-station search, Census and NWS field extraction, overrides (validation, name, tier, water edge, and restoring the OSM name), the SQLite export (tables, index, blob, identical bytes on a rebuild), diff categories, nearby and water asked by box (cells, widened boxes, the rewritten query, the count check, the merge, resuming a tile part by part), and tidal datum gaps filled from the nearest station.
