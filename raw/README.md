# raw/

Everything a pull fetches lands here, one folder per run date. This folder is git-ignored except this README.

| Path | What |
|---|---|
| `raw/<run-date>/osm/<tile>.json` | Overpass beach query per tile: query, endpoint, fetch time, full unsimplified geometry, OSM metadata (including editor names, which never go to `pack/`), and a `source_hash` per element |
| `raw/<run-date>/osm_nearby/<tile>.json` | Overpass nearby query per tile |
| `raw/<run-date>/tides/stations.json`, `raw/<run-date>/buoys/station_table.json` | the two station downloads |
| `raw/<run-date>/<source>/<beach id>.json` | one per-point response (`census`, `padus`, `nws`, `wikipedia`) with its URL, fetch time, and `response_hash` |
| `raw/<run-date>/wikidata/batch_NNNN.json` | Wikidata facts for up to 50 QIDs, each with a `source_hash` |
| `raw/<run-date>/_status/<source>.json` | what the last pull of that source did: calls, ok, errors, cached, seconds, endpoint, note. A block counts as pulled in this run when one of its sources has a status file here. |

Keep a run's folder until its `pack/` is reviewed and committed. A pull that is interrupted resumes from what is already here.
