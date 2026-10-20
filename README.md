# BeachX.pack

A re-runnable catalog of every beach on the US coastline (Atlantic, Gulf, Pacific, Alaska, Hawaii, the Great Lakes, plus Puerto Rico and the US Virgin Islands), built mainly from OpenStreetMap. Each beach gets categorization facts, a rank tier, a simplified boundary, an official 5 m grid, nearby webcams and surf spots, and enrichment from NOAA, the Census, USGS PAD-US, the National Weather Service, Wikidata, and Wikipedia. The Beachs app (code name BeachX) consumes the "pack" built from it.

This is a tool a person runs and reviews. It is not a service.

Data © OpenStreetMap contributors, available under the Open Database License (ODbL 1.0). See [Sources](#sources) for every other source and its license.

## Requirements

Python 3.13, standard library only. No `pip install`.

## Running it

The one-command yearly re-run:

```sh
python3 -m beachx_pack run        # pull every block for every tile, then build pack/
python3 -m beachx_pack diff       # what changed against the committed pack, block by block
python3 -m beachx_pack stats      # block table, counts, top 25, largest grids
```

Blocks have their own cadences (below), so between yearly runs you can refresh only what is due:

```sh
python3 -m beachx_pack pull --due && python3 -m beachx_pack build
```

Then follow [Review before you commit](#review-before-you-commit).

## Blocks

Each beach's data is split into named blocks. A pull runs or skips whole blocks. `build` always rebuilds every record. It first reads the previous pack from disk (what git has: `pack/beaches.json` plus `pack/boundaries/`, `pack/grids/`, and `pack/nearby/`). A block pulled in this run comes from `raw/<run-date>/`; every other block is carried forward from the previous pack with its old hash and `pulled_at`; a block that was never pulled is null (listed as `never run` in the manifest). The new pack is written at the end. There is no separate cache: the pack is the cache. The table lives in `beachx_pack/blocks.py`.

| Block | Cadence | Sources | Who gets it | Fields |
|---|---|---|---|---|
| `geometry` | 365 days | OSM `natural=beach`, `leisure=beach_resort` (Overpass) | every beach | the core fields, boundary, area, grid; score and tier are computed from all blocks |
| `nearby` | 90 days | OSM webcams, surf spots, surf shops, lifeguard posts, parking, toilets, showers, drinking water (Overpass) | every beach | `nearby_counts`, `has_webcam`, `has_surf_spot`, `pack/nearby/` |
| `stations` | 365 days | NOAA CO-OPS tide station list, NOAA NDBC station table (two downloads) | every beach | `tide_station_*`, `wave_buoy_*` |
| `places` | 730 days | US Census Geocoder (per point), built-in time zone table | Census: primary and secondary; time zone: every beach | `county`, `county_fips`, `place`, `place_fips`, `census_state`, `timezone` |
| `land` | 365 days | USGS PAD-US (per point) | primary and secondary | `public_access`, `land_manager_type`, `land_manager_name`, `designation`, `padus_unit_name` |
| `weather` | 365 days | NWS `api.weather.gov/points` (per point) | primary and secondary | `nws_forecast_zone`, `nws_county_zone`, `nws_office`, `nws_grid` |
| `health` | 365 days | EPA BEACON | not pulled yet (see [Not pulled yet](#not-pulled-yet)) | `beacon_beach_id`, `beacon_match_confidence` |
| `wiki` | 90 days | Wikidata (batches of 50), Wikipedia extracts (per point) | Wikidata: primary and secondary with a `wikidata` tag; Wikipedia: primary with a `wikipedia` tag | `wikidata_label`, `wikidata_sitelinks`, `wikidata_is_beach`, `wikidata_is_park`, `description`, `description_license`, `description_source_url` |

`geometry` is the primary block: everything keys off its beach ids. Per-point blocks need tiers, so `pull` ranks the beaches first (from this run's geometry and whatever blocks are already cached) and looks up primary beaches first, then secondary, by score.

Every beach record carries `blocks: {"geometry": {"hash": "...", "pulled_at": "YYYY-MM-DD"}, "nearby": {...}, ...}` (null for a block it does not have). The manifest carries, per block, `pack_hash`, `last_run`, `cadence_days`, `beaches`, `status`, and per-source call counts and timings.

### Hashes

- `geometry`: SHA-256 over the OSM element's type, id, sorted tags, and geometry (node point, way points, relation members with role and points), floats fixed to 7 decimals. Computed at pull time and stored on the raw element as `source_hash`. When it matches the beach's geometry hash in the previous `pack/beaches.json` (and the simplify and grid settings, recorded as `derive` in the manifest, are unchanged), that beach's area, boundary, and grid are taken from the previous pack instead of recomputed. `build` prints how many shapes were reused versus computed.
- `nearby`: SHA-256 over the attached items' type, id, version, and sorted tags. Kept apart from geometry, so a webcam appearing or a shop closing never invalidates a grid.
- Every other block: SHA-256 over the block's output fields for that beach.
- Each raw per-point response is stored with its own `response_hash`; each Wikidata entity with a `source_hash`.
- `manifest.json` has a `pack_hash` per block (SHA-256 over sorted `[id, hash]` pairs) and a top-level `pack_hash` over all of them, so two packs compare with one string.

## Commands

All commands run from the repo root as `python3 -m beachx_pack <command>`.

| Command | What it does |
|---|---|
| `pull [--tiles ...] [--run-date YYYY-MM-DD] [--force] [--blocks a,b] [--skip-blocks a,b] [--due] [--sources a,b] [--skip-sources a,b] [--limit N] [--wikidata-limit N] [--wikipedia-limit N] [--endpoint URL]` | Fetches the chosen blocks into `raw/<run-date>/`. `--tiles` (geometry and nearby) takes `all` (default), a region (`hawaii`, `alaska`, `atlantic`, `gulf`, `pacific`, `great_lakes`, `territories`), or a comma list of regions and USPS codes; a state code selects every tile its box touches. `--due` keeps only blocks whose cadence has elapsed since the manifest's `last_run` (every block is due when there is no pack). `--limit` caps per-point calls per source. Sources: `osm`, `osm_nearby`, `tides`, `buoys`, `timezone`, `census`, `padus`, `beacon`, `nws`, `wikidata`, `wikipedia`. |
| `build [--run-date]` | `raw/<run-date>/` (default: the latest) merged over the previous `pack/`, written back to `pack/`. |
| `run` | `pull`, then `build`. Same options as `pull`. |
| `diff [--against old/beaches.json]` | Compares `pack/` with a previous pack (default: git `HEAD`). Per block: unchanged, changed, added, removed. For changed geometry: renamed, moved more than 250 m, with old and new `osm_version` and timestamp. Tier changes for every beach. Nearby items added or removed by name. Samples of each. |
| `export --dart PATH [--tier primary] [--class-name PackBeaches]` | A Dart seed class in the app's `StarterBeaches` style. Unnamed beaches are skipped. |
| `export --json PATH --tier primary\|secondary\|minor\|all [--boundaries] [--grids] [--nearby]` | A trimmed app asset (see [App JSON](#app-json)). |
| `stats` | The block table (last run, cadence, due, beaches covered, per-source calls), counts by tier, state, state confidence, and surface, coverage of each field, the top 25 by score, and the five largest grids by cell count. |

### How long a run takes, and resuming

Measured on the Hawaii validation run (2026-10-20, 4 tiles, 472 beaches, overpass-api.de heavily loaded that day):

| Step | Calls | Time | Per call |
|---|---|---|---|
| geometry (Overpass beach query) | 4 | 35–49 s each, plus 140 s of retry waits on one tile | ~40 s |
| nearby (Overpass nearby query) | 4 + 3 re-fetches | 35–214 s each; the canary rejected two broken answers | ~90 s |
| stations: tides, buoys | 1 + 1 | 0.5 s, 0.1 s | |
| places: Census | 10 (`--limit 10`) | 8.5 s | 0.85 s |
| places: time zone | 0 | instant | |
| land: PAD-US | 10 (`--limit 10`) | 97.5 s | 9.75 s |
| weather: NWS | 10 (`--limit 10`) | 8.9 s | 0.9 s |
| health: BEACON | 0 | not pulled | |
| wiki: Wikidata | 2 batches (91 QIDs) | ~3 s | ~1.5 s |
| wiki: Wikipedia | 5 | 3.3 s | 0.66 s |
| build | | 0.2–0.3 s for 472 beaches, cold or cached | |

Estimate for a full run (`python3 -m beachx_pack run`):

- Overpass: 128 tiles × 2 queries. At Hawaii's ~140 s per tile (both queries plus the 5 s pauses) that is about 5 hours; with retries on a busy day, and mainland tiles (Florida, California, the Great Lakes) being denser, plan on 6–10 hours. On a quiet server it can be 2–3 hours.
- Beaches: Hawaii averaged 118 per tile; mainland tiles are denser, so expect roughly 20,000–40,000 beaches. About 24% were primary or secondary in Hawaii, so 5,000–10,000 per-point lookups per source.
- Per-point sources, at the measured rates: Census 1–2.5 hours, NWS 1.3–2.5 hours, PAD-US 14–27 hours (its ArcGIS layer took about 9.5 s per point query), Wikipedia a few minutes (primary beaches with a `wikipedia` tag only), Wikidata about a minute.
- Total: roughly one to two days of wall time, most of it PAD-US. Every step resumes, so it can run across sessions: for example `pull --skip-blocks land` first, then `pull --blocks land` over a few evenings (same `--run-date`), then `build`.

Every pull is resumable. Tile responses go to `raw/<run-date>/osm/<tile>.json` and `raw/<run-date>/osm_nearby/<tile>.json`, per-point responses to `raw/<run-date>/<source>/<beach id>.json`, each written atomically as soon as it arrives. Anything already there for that run date is never fetched again unless you pass `--force`. If a pull dies or a tile fails after its retries, run the same command again with the same `--run-date`. `run` refuses to build until every selected tile is present. A source that is unreachable (three failures in a row, or a failed download) is skipped with a note in `raw/<run-date>/_status/<source>.json` and in the manifest; it never stops the pull.

## Outputs

`raw/` is git-ignored (except `raw/README.md`). `pack/` is committed.

| Path | What |
|---|---|
| `pack/beaches.json` | One record per beach, sorted by state, then name, then id. A JSON array with one record per line, so git diffs stay readable. |
| `pack/beaches.csv` | The same records without `tags`, `blocks`, `nearby_counts`, and `nws_grid` (bbox split into four columns, alt names joined with `; `). |
| `pack/boundaries/<state>.json` | Simplified boundaries keyed by beach id. |
| `pack/grids/<state>.json` | Official grids keyed by beach id. |
| `pack/nearby/<state>.json` | Nearby items keyed by beach id. |
| `pack/manifest.json` | Run date, pipeline version, `pack_hash`, sources with URLs, licenses and attribution, the block table, tile ids, counts. No wall-clock time, so rebuilding the same inputs gives identical files. |

Size: the Hawaii pack (472 beaches) is about 3 MB (`beaches.json` 1.0 MB, `grids/` 0.9 MB, `nearby/` 0.6 MB, `boundaries/` 0.3 MB). At national scale the committed pack is likely 150 to 250 MB of JSON across the per-state files. Git compresses JSON like this well, and keeping one file per state keeps diffs readable.

## Sources

| Source | Block | Endpoint | License | Attribution |
|---|---|---|---|---|
| OpenStreetMap via Overpass | geometry, nearby | `https://overpass-api.de/api/interpreter`, falling back to `https://overpass.kumi.systems/api/interpreter` | ODbL 1.0 | "© OpenStreetMap contributors" |
| NOAA CO-OPS tide prediction stations | stations | `https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json?type=tidepredictions` | US public domain | NOAA CO-OPS |
| NOAA NDBC station table | stations | `https://www.ndbc.noaa.gov/data/stations/station_table.txt` | US public domain | NOAA NDBC |
| US Census Geocoder | places | `https://geocoding.geo.census.gov/geocoder/geographies/coordinates` (`benchmark=Public_AR_Current`, `vintage=Current_Current`) | US public domain | US Census Bureau |
| Time zone table | places | built in (`sources/timezone.py`) | n/a | IANA zone names |
| USGS PAD-US, "PAD-US Public Access" layer | land | `https://services.arcgis.com/v01gqwM5QqNysAAi/arcgis/rest/services/PADUS_Public_Access/FeatureServer/0/query` | US public domain | USGS Gap Analysis Project |
| NWS API | weather | `https://api.weather.gov/points/<lat>,<lng>` | US public domain | NOAA National Weather Service |
| Wikidata | wiki | `https://www.wikidata.org/w/api.php` (`wbgetentities`) | CC0 1.0 | Wikidata |
| Wikipedia | wiki | `https://<lang>.wikipedia.org/w/api.php` (`prop=extracts`, `exintro`, `explaintext`) | CC BY-SA 4.0 | Wikipedia contributors; each `description` carries `description_source_url` |

Nominatim is not used. Every request sends `User-Agent: BeachX.pack catalog, hello@apphaus.us`. Overpass gets one query at a time, a 5 s pause between queries, and retries with doubling backoff (20 s, 40 s, 80 s) on 429, 5xx, timeouts, and Overpass "runtime error" remarks, then the fallback endpoint. Per-point sources run one request at a time with a short pause (Census and NWS 0.5 s, PAD-US 0.3 s, Wikipedia 0.5 s) and 3 tries with backoff. Wikidata sends `maxlag=5`.

Wikipedia text is CC BY-SA 4.0, which is share-alike. It sits in `description` with its license and source URL on every record; keep that attribution wherever the app shows it.

### Not pulled yet

EPA BEACON (`health` block). What was checked on 2026-10-20:

- `https://beacon.epa.gov/ords/beacon2/` is an Oracle APEX web app (BEACON 2.0). Its pages render HTML reports; no documented REST or download endpoint for a list of monitored beaches with coordinates was found, and the guessed ORDS catalog paths (`open-api-catalog/`, `metadata-catalog/`) do not answer.
- `https://watersgeo.epa.gov/arcgis/rest/services/BEACON` returns "Folder not found". The "EPA Beach Advisory Viewer" at `https://watersgeo.epa.gov/beacon2/` is a web map without a listed public layer.
- ArcGIS Online has `WQP_Beaches` (`https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/WQP_Beaches/FeatureServer/0`, 7,307 points with `org_id`, `mloc_id`, `mloc_name`, latitude, longitude). It is published from an individual EPA staff account, and its ids are Water Quality Portal monitoring-location ids, not BEACON beach ids. It could become a source if EPA confirms it is maintained.

The `health` block therefore stays `never run`; `sources/beacon.py` records the note in the manifest.

### Tiles

Tiles are hand-written in `beachx_pack/tiles.py` from each state's coastal extent: roughly 2° by 2°, and 2° by 4° in Alaska (at 51–72° N that is about the same ground size). Inland tiles are skipped. The Aleutians are split at the 180° meridian. The Northwestern Hawaiian Islands are not covered. There are 128 tiles: Hawaii 4, Atlantic 20, Gulf 13, Pacific 16, Great Lakes 20, Alaska 52, territories 3. Tiles overlap in places; elements are deduplicated by OSM type and id.

A 2° tile reaches well inland, so lake and river beaches inside a coastal tile are included. There is no coastal-versus-inland flag.

Tiles also reach into Canada, Mexico, the Bahamas, and the British Virgin Islands. Each tile query asks Overpass which of its beaches lie inside the US country area (`ISO3166-1=US`, or `ISO3166-2=US-PR` / `US-VI` for territory tiles). Beaches outside it are dropped and counted as `excluded_foreign`. If a tile's country area does not resolve, nothing is dropped and the beach is counted as `country_area_unresolved`.

## Fields

Every field comes from a source or from a rule written here. Unknown is `null`, never a guess.

### geometry

| Field | Source or rule |
|---|---|
| `id` | `beach_osm_w<id>` for ways, `beach_osm_r<id>` for relations, `beach_osm_n<id>` for nodes. Stable across pulls. |
| `osm_type`, `osm_id` | OSM element |
| `osm_url`, `osm_edit_url` | `https://www.openstreetmap.org/<type>/<id>`, `https://www.openstreetmap.org/edit?<type>=<id>` |
| `osm_version`, `osm_timestamp`, `osm_changeset` | OSM metadata (`out meta`). The editor's user name and uid stay in `raw/` only. |
| `name` | `name` tag |
| `alt_names` | `alt_name` (split on `;`), `official_name`, `name:en`, minus duplicates of `name`; `null` when empty |
| `latitude`, `longitude` | Nodes: the node. Ways and relations: the centre of the element's bounding box (the point Overpass `out center` returns). |
| `bbox` | `[south, west, north, east]` from Overpass bounds; `null` for nodes |
| `area_m2` | Shoelace area of the unsimplified outer ring(s) minus inner rings, in the local frame below, around `latitude`/`longitude`. `null` for nodes, open ways, and relations whose rings do not close. |
| `state`, `state_confidence` | See [State](#state). |
| `country` | `US`, `PR`, or `VI`, from the country area the beach lies in |
| `surface`, `access`, `fee`, `dog`, `wheelchair`, `operator` | the tag of the same name |
| `lifeguard` | `lifeguard` tag, else `supervised` |
| `nudist` | `nudism` tag |
| `website` | `website` tag, else `contact:website` |
| `wikipedia` | the tag, as is |
| `wikipedia_url` | `<lang>:<title>` becomes `https://<lang>.wikipedia.org/wiki/<title>` with spaces as underscores, percent-encoded; `null` without a language prefix |
| `wikidata`, `wikidata_url` | the tag when it is exactly one QID (else `null`; the raw value stays in `tags`), and `https://www.wikidata.org/wiki/<QID>` |
| `is_resort` | `leisure=beach_resort` |
| `protected_area_hint` | `name` or `operator` contains (case-insensitive) one of: state park, state beach, state recreation area, state seashore, national park, national seashore, national lakeshore, national recreation area, national wildlife refuge, wildlife refuge, national monument, county park, regional park, recreation area, beach park, nature preserve, preserve, reserve, wildlife area, park service, parks and recreation, department of parks, department of natural resources, fish and wildlife; or the element is tagged `boundary=protected_area`, `boundary=national_park`, `leisure=nature_reserve`, or `leisure=park`. (`PROTECTED_WORDS` in `normalize.py`.) |
| `osm_tags_count` | number of OSM tags |
| `has_boundary`, `boundary_points`, `has_grid`, `cell_count` | see [Boundaries](#boundaries) and [Grids](#grids) |
| `score`, `tier`, `seed_space` | see [Tiers](#tiers) |
| `tiles` | ids of the tiles the element came from (a beach whose tiles are re-pulled and that is no longer there is removed) |
| `last_pulled` | the geometry block's `pulled_at` |
| `tags` | the element's full raw OSM tag map, unchanged |

`parking_nearby` from the first brief is covered by the nearby block instead.

### Other blocks

| Field | Source or rule |
|---|---|
| `nearby_counts` | items within 300 m, by kind (only kinds with at least one) |
| `has_webcam`, `has_surf_spot` | at least one item of that kind |
| `tide_station_id`, `tide_station_name`, `tide_station_distance_km` | nearest NOAA CO-OPS tide prediction station (great-circle distance from `latitude`/`longitude`) |
| `wave_buoy_id`, `wave_buoy_name`, `wave_buoy_distance_km` | nearest NDBC station whose type contains "buoy", excluding DART, drifting, Atlas, and tsunami types. It is not checked that the buoy currently reports waves. |
| `county`, `county_fips` | Census geocoder `Counties` NAME and GEOID (5 digits) |
| `place`, `place_fips` | Census `Incorporated Places`, else `Census Designated Places`, NAME and GEOID (7 digits) |
| `census_state` | Census `States` STUSAB. When present it replaces `state`, and `state_confidence` becomes `census`. |
| `timezone` | IANA zone from the built-in table. Splits: Florida west of -85.0 is America/Chicago (the Apalachicola River approximated by -85.0; Port St. Joe, Cape San Blas, and the St. Joseph Peninsula are, as far as we know, Eastern but fall west of it); Alaska west of -169.5 and south of 55° N, or east of 180°, is America/Adak (the Pribilofs and St. Lawrence Island stay America/Anchorage); Michigan's Menominee County shore (south of 46° N, west of -87.35) and Gogebic County shore (west of -89.36) are America/Menominee; Indiana's Lake Michigan shore (west of -86.52) is America/Chicago. Hawaii Pacific/Honolulu, PR America/Puerto_Rico, VI America/St_Thomas. |
| `public_access`, `land_manager_type`, `land_manager_name`, `designation`, `padus_unit_name` | PAD-US `Pub_Access` (OA, RA, XA, UK become Open, Restricted, Closed, Unknown), `MngTp_Desc`, `MngNm_Desc`, `DesTp_Desc`, `Unit_Nm`, for units within 30 m of the lookup point. The layer's "Non-PAD-US Area" filler polygons are ignored. When several units match: Fee before Easement before Designation before Proclamation, then the smallest `GIS_Acres`. All null when no unit matches. |
| `nws_forecast_zone`, `nws_county_zone`, `nws_office`, `nws_grid` | NWS points: last path segment of `forecastZone` and `county`, `cwa`, and `{gridId, gridX, gridY}`. A 404 (no forecast at that point) gives nulls. |
| `beacon_beach_id`, `beacon_match_confidence` | not pulled yet |
| `wikidata_label`, `wikidata_sitelinks` | English label, number of sitelinks |
| `wikidata_is_beach` | `P31` includes beach (Q40080) or state beach (Q35800652) |
| `wikidata_is_park` | `P31` includes park, urban park, protected area, national park, nature reserve, state park, county park, regional park, National Seashore, National Recreation Area, state recreation area, National Wildlife Refuge, or marine protected area (QIDs in `sources/wikidata.py`) |
| `description`, `description_license`, `description_source_url` | first paragraph of the article's plain-text intro, "CC BY-SA 4.0", and the article URL (after redirects) |

Per-point lookups use a point inside the beach polygon (the middle of the widest inside span on a latitude line through the polygon), not the bounding-box centre, which can sit offshore. Nodes and open ways use `latitude`/`longitude`.

### State

1. If the beach lies in exactly one OSM state area (`ISO3166-2=US-XX`, asked of Overpass for every state whose box touches the tile): that state, `area`.
2. If it lies in more than one: the `addr:state` / `is_in:state` / `is_in:state_code` tag when it names one of them (`tag`), else the tile's primary state if it is one of them, else the first alphabetically (`area`).
3. If it lies in none: the tag, if it names a known state (`tag`).
4. Otherwise the built-in state box table (`STATE_BOXES` in `tiles.py`, hand-entered approximations of US Census state extents, widened by 0.1°): the only box, or the tile's primary state when boxes overlap, else the first alphabetically (`bbox`).
5. When the `places` block has a Census answer, that wins (`census`).

## Boundaries

For each way and multipolygon relation:

1. Build the outer ring(s) from `out geom` coordinates as `[lat, lng]` pairs. Relation members with role `inner` become holes; all other way members are outer. Pieces are joined end to end (reversing when needed). Pieces that never close are dropped.
2. Close each ring and drop duplicate consecutive points. A ring needs 3 distinct points.
3. Simplify with Douglas–Peucker in local metres at a 3 m tolerance. A closed ring is split at its first point and the point farthest from it, and each half is simplified. Coordinates are rounded to 7 decimals.

An open way (a beach drawn as a line) gets no boundary and no area; it is not closed artificially. Nodes have no boundary.

```json
{"beach_osm_w123": {"rings": [[[21.27, -157.82], ...]], "point_count": 42, "simplified_from": 310, "holes": [[[...]]]}}
```

`holes` appears only when the beach has inner rings. Raw, unsimplified geometry stays in `raw/`.

The local frame uses the app's constants: `east = (lng - lng0) * 111320 * cos(lat0)`, `north = (lat - lat0) * 110540`.

## Grids

Grids are official, fixed, and computed here. The app registers a beach's grid as a Space on arrival and never generates cells. It takes the anchor, heading, cell size, and cell set exactly as given, and then only maps its own GPS and AR pose into that fixed frame to know which cell it is standing on and where to draw. A grid is shaped to the sand; it is never a bounding square.

For every beach with a boundary:

- `cell_size_m` = 5.
- `anchor` = the area-weighted centroid of the simplified outer ring(s), rounded to 7 decimals.
- `heading_degrees` = the first principal axis (PCA) of the simplified outer ring points in local metres around the anchor, as degrees clockwise from true north, folded into [0, 180), rounded to 0.1°. Cells run along the shore.
- Frame, with `h` the heading in radians and east/north from the anchor as above: `x = east*cos(h) - north*sin(h)`, `y = east*sin(h) + north*cos(h)`; inverse `east = x*cos(h) + y*sin(h)`, `north = -x*sin(h) + y*cos(h)`.
- A cell `(cx, cy) = (floor(x/5), floor(y/5))` is in the grid when its centre `(cx*5 + 2.5, cy*5 + 2.5)` lies inside an outer ring and outside every hole (ray casting, even-odd).
- The computation uses the rounded anchor, the rounded heading, and the rounded simplified rings, so the stored numbers reproduce the cell set exactly.

```json
{"beach_osm_w123": {"anchor": {"latitude": 21.2767, "longitude": -157.8278, "heading_degrees": 30.0},
  "cell_size_m": 5, "cell_count": 320, "x_min": -4, "x_max": 3, "y_min": -20, "y_max": 19,
  "rows": [[-20, [[-4, 3]]], [-19, [[-4, 3]]], ...]}}
```

Rows are run-length encoded: each span is inclusive, rows are sorted by `cy`, spans by `x0`. No beach is skipped for size.

## Nearby

A second Overpass query per tile pulls, with `out meta center`:

| Kind | OSM |
|---|---|
| `webcam` | `man_made=surveillance` + `surveillance:type=webcam`, or any element with `contact:webcam`, `webcam`, or `url:webcam` |
| `lifeguard` | `emergency=lifeguard` |
| `surf_shop` | `shop=surf`, `amenity=surf_school`, or `sport=surfing` on something with a `shop` or `club` tag |
| `surf_spot` | other `sport=surfing`, or a named `natural=reef` |
| `parking`, `toilets`, `shower`, `drinking_water` | `amenity=` the same |

An item gets the first kind that matches in that order. Overpass already limits the query to items within 300 m of a beach in the tile (`around`); the build then measures the exact distance in local metres from the item's point to the nearest boundary edge (0 inside the polygon), or, for nodes and open ways (which have no polygon), to the beach point, and attaches the item to every beach within 300 m.

`pack/nearby/<state>.json`, keyed by beach id, sorted by kind, distance, and id:

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

The per-point blocks pick their beaches by tier before they run, so the tier used for that choice comes from the data available at that moment (for the first run, without PAD-US and Wikidata sitelinks). A named beach with a `wikidata` tag scores at least 6, so it is never left out of the Wikidata lookup.

## App JSON

`export --json PATH --tier T` writes `{"run_date", "attribution", "count", "beaches": [...]}` with, per beach: `id, name, latitude, longitude, state, tier, surface, access, lifeguard, seed_space, osm_type, osm_id, osm_url, osm_version, osm_timestamp, wikidata, wikipedia_url, timezone, tide_station_id, wave_buoy_id, county, place, public_access, land_manager_type, designation, nws_forecast_zone, nws_grid, beacon_beach_id, description`. `--boundaries`, `--grids`, and `--nearby` add `boundary`, `grid`, and `nearby` for the selected tier.

## Dart export

`python3 -m beachx_pack export --dart raw/PackBeaches.dart --tier primary` writes the app's seed shape. The app side wires it in later, next to `lib/src/catalog/starter_beaches.dart`:

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

## Review before you commit

1. `python3 -m beachx_pack diff`. Large `removed` or `moved` counts usually mean a tile failed or a mapper restructured a beach. Open the changesets it prints.
2. `python3 -m beachx_pack stats`. Check the block table (every block you pulled shows today's date, nothing unexpected is `never run`, source notes make sense), the tier and state counts against the last `pack/manifest.json`, and `excluded_foreign` / `country_area_unresolved` in the manifest.
3. Look at the top 25 (from `stats`) and a random 25:
   `python3 -c "import json,random; b=json.load(open('pack/beaches.json')); [print(r['tier'], r['state'], r['name'], r['osm_url']) for r in random.sample(b, 25)]"`.
4. Spot-check one big grid against its boundary, and a few webcam URLs.
5. `python3 -m unittest`.
6. Commit `pack/` and any code changes. Do not commit `raw/`. The committed pack is also what the next build reuses.

## Tests

`python3 -m unittest` runs offline on fixture snippets: tile ids and dedupe, state box lookup, ids and links, score and tier thresholds (including PAD-US replacing the name hint, the designation point, and the capped nearby group), the ring builder (a 100 m square way gives about 10,000 m²), the simplifier, grids (a 200 m by 40 m rectangle at 30° gives heading 30.0 and 320 cells, all inside; a hole's cells are excluded), the geometry block, a second build that reuses every shape from the previous pack and writes a byte-identical `beaches.json`, a block carried forward from the previous pack with its old date, block cadence and `--due`, the time zone rules, the NDBC table parser, the nearest-station search, PAD-US, Census and NWS field extraction, and diff categories.
