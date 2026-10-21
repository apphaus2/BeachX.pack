# raw/

Everything a pull fetches lands here, one folder per state and run date, plus the whole-file downloads every state shares. This folder is git-ignored except this README.

| Path | What |
|---|---|
| `raw/<st>/<run-date>/osm/<tile>.json` | Overpass beach query per tile: query, endpoint, fetch time, full unsimplified geometry, OSM metadata (including editor names, which never go to `pack/`), and a `source_hash` per element |
| `raw/<st>/<run-date>/osm_nearby/<tile>.json` | Overpass nearby query per tile |
| `raw/<st>/<run-date>/osm_water/<tile>.json` | Overpass coastline and water polygons near the tile's beaches |
| `raw/<st>/<run-date>/tides/stations.json`, `buoys/station_table.json` | the two station downloads |
| `raw/<st>/<run-date>/datums/<station>.json`, `waves/<buoy>.json` | tidal datums per station, median wave height per buoy |
| `raw/<st>/<run-date>/<source>/<beach id>.json` | one per-point response (`census`, `nws`, `wikipedia`) with its URL, fetch time, and `response_hash` |
| `raw/<st>/<run-date>/padus/lookup.json`, `gazetteer/nearest.json` | the local lookups for every beach |
| `raw/<st>/<run-date>/wikidata/batch_NNNN.json` | Wikidata facts for up to 50 QIDs, each with a `source_hash` |
| `raw/<st>/<run-date>/terrain/<beach id>.json` | terrain fields and one height per cell, with the grid key they were measured for |
| `raw/<st>/<run-date>/terrain/clusters/<id>.json`, `terrain/rasters/<id>_<n>.tif` | each 3DEP cluster's beaches, catalog items, and raster pieces, kept so a changed grid is re-measured without the network |
| `raw/<st>/<run-date>/_status/<source>.json` | what the last pull of that source did: calls, ok, errors, cached, seconds, endpoint, note. A block counts as pulled in this run when one of its sources has a status file here. |
| `raw/downloads/padus/PADUS4_1_State_<ST>_GDB_KMZ.zip` | the PAD-US state download (or `<ST>_export.geojson`, the fallback) |
| `raw/downloads/tiger/tl_2024_us_state.zip` | Census TIGER/Line state boundaries |
| `raw/downloads/gazetteer/2025_Gaz_place_national.zip` | Census Gazetteer places |

Keep a run's folder until its pack is reviewed and committed; keep the latest run with terrain rasters if you may change grid rules or add water-edge overrides. A pull that is interrupted resumes from what is already here.
