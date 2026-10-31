# raw/

`raw/` holds everything a pull fetches. It is about 2 GB for three states and grows to tens of gigabytes nationally, so it lives on the network share and `raw` in this folder is a link to it. Git ignores `raw` completely.

The share is `//192.168.0.4/smorey2`. The folder on it is `_APPS/BeachX.pack/raw`.

## Make the link

Run from this folder, the one that holds `RAW.md`. Mount the share first. A pull or build with the share unmounted fails, because the link points at nothing.

macOS (mount in Finder with Go, Connect to Server, `smb://192.168.0.4/smorey2`):

```sh
ln -s /Volumes/smorey2/_APPS/BeachX.pack/raw raw
```

Windows, in Command Prompt run as administrator or with Developer Mode on:

```bat
mklink /D raw \\192.168.0.4\smorey2\_APPS\BeachX.pack\raw
```

Windows, in PowerShell with the same rights:

```powershell
New-Item -ItemType SymbolicLink -Path raw -Target \\192.168.0.4\smorey2\_APPS\BeachX.pack\raw
```

Check it with `ls raw` on macOS or `dir raw` on Windows; you should see `downloads` and one folder per state. Never pull the same state from two machines at once: both write into the same run folder.

## Layout

One folder per state and run month (`YYYY-MM`, UTC), plus the whole-file downloads every state shares. A pull writes into the current month's folder and resumes from whatever is already there, so a pull continued on another day of the month picks up where it stopped. Re-fetching within the same month needs `--force`.

| Path | What |
|---|---|
| `raw/<st>/<run-month>/osm/<tile>.json` | Overpass beach query per tile: query, endpoint, fetch time, full unsimplified geometry, OSM metadata (including editor names, which never go to `pack/`), and a `source_hash` per element |
| `raw/<st>/<run-month>/osm_nearby/<tile>.json` | Overpass nearby query per tile |
| `raw/<st>/<run-month>/osm_water/<tile>.json` | Overpass coastline and water polygons near the tile's beaches |
| `raw/<st>/<run-month>/tides/stations.json`, `buoys/station_table.json` | the two station downloads |
| `raw/<st>/<run-month>/datums/<station>.json`, `waves/<buoy>.json` | tidal datums per station, median wave height per buoy |
| `raw/<st>/<run-month>/<source>/<beach id>.json` | one per-point response (`census`, `nws`, `wikipedia`) with its URL, fetch time, and `response_hash` |
| `raw/<st>/<run-month>/padus/lookup.json`, `gazetteer/nearest.json` | the local lookups for every beach |
| `raw/<st>/<run-month>/wikidata/batch_NNNN.json` | Wikidata facts for up to 50 QIDs, each with a `source_hash` |
| `raw/<st>/<run-month>/terrain/<beach id>.json` | terrain fields and one height per cell, with the grid key they were measured for |
| `raw/<st>/<run-month>/terrain/clusters/<id>.json`, `terrain/rasters/<id>_<n>.tif` | each 3DEP cluster's beaches, catalog items, and raster pieces, kept so a changed grid is re-measured without the network |
| `raw/<st>/<run-month>/_status/<source>.json` | what the last pull of that source did: date, calls, ok, errors, cached, seconds, endpoint, note. A block counts as pulled in this run when one of its sources has a status file here. The newest `date` is the run date the build stamps on the pack. |
| `raw/downloads/padus/PADUS4_1_State_<ST>_GDB_KMZ.zip` | the PAD-US state download (or `<ST>_export.geojson`, the fallback) |
| `raw/downloads/tiger/tl_2024_us_state.zip` | Census TIGER/Line state boundaries |
| `raw/downloads/gazetteer/2025_Gaz_place_national.zip` | Census Gazetteer places |

Keep a run's folder until its pack is reviewed and committed; keep the latest run with terrain rasters if you may change grid rules or add water-edge overrides.
