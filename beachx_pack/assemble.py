"""Merge every block into beach records: this run's raw/ where a block was
pulled, the previous pack otherwise, null when a block was never pulled."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from . import cells
from . import overrides as overrideslib
from . import nearby as nearbylib
from . import normalize, overpass
from .blocks import BLOCKS, BY_NAME, read_statuses
from .hashing import sha256
from .score import score, tier
from .sources import MODULES
from .tiles import TILES

SHAPE_FIELDS = ("area_m2", "has_boundary", "boundary_points", "has_grid", "cell_count")


def _ran(statuses: dict) -> set[str]:
    return {s["block"] for s in statuses.values() if not s.get("skipped")}


def _attach_shape(rec: dict, shp: dict) -> dict:
    point = (rec["latitude"], rec["longitude"])
    bbox = tuple(rec["bbox"]) if rec["bbox"] else (point[0], point[1], point[0], point[1])
    if shp.get("boundary"):
        b = shp["boundary"]
        return {"kind": "polygon", "bbox": bbox, "rings": [[tuple(p) for p in r] for r in b["rings"]],
                "holes": [[tuple(p) for p in r] for r in b.get("holes", [])]}
    return {"kind": "point", "bbox": bbox, "point": point}


def _geometry(raw_dir: Path, run_date: str, ran: set, cached: dict, light: bool,
              stats: Counter, overrides: dict | None = None) -> tuple[dict, Counter]:
    # Tiles on disk count even while the Overpass queue is still running: each
    # tile file is complete (written atomically), and only beaches whose own
    # tiles were pulled can be removed.
    tiles = overpass.load_tiles(raw_dir, "osm")
    if tiles:
        ran.add("geometry")
    pulled = {t["tile"]["id"] for t in tiles}
    old = {rid: e["geometry"] for rid, e in cached.items() if e.get("geometry")}
    water = overpass.load_tiles(raw_dir, "osm_water")
    fresh, report = normalize.geometry_blocks(tiles, old, light, water, overrides)
    geo = {rid: {**blk, "pulled_at": run_date} for rid, blk in fresh.items()}
    for rid, blk in fresh.items():
        stats["geometry_unchanged" if old.get(rid, {}).get("hash") == blk["hash"]
              else "geometry_changed"] += 1
    for rid, blk in old.items():
        if rid in geo:
            continue
        if pulled & set(blk["data"]["tiles"]):
            stats["geometry_removed"] += 1  # its tiles were pulled and it is gone
        else:
            geo[rid] = blk
            stats["geometry_kept"] += 1
    return geo, report


def assemble(raw_dir: Path, run_date: str, cached: dict[str, dict], *, light: bool = False,
             keep=None, overrides: dict | None = None):
    """Returns (records, extras, block_stats, report).

    `cached` is previous.load(pack_dir)[0]: the previous pack's blocks per beach.
    extras: {"boundaries": {id: ...}, "grids": {id: ...}, "nearby": {id: [...]}}.
    `light` (used by pull to rank beaches) skips grids and nearby matching.
    `keep(rec)` decides the state: it returns (state, country, confidence) for
    a beach in the pack's state and None for one that belongs elsewhere.
    `overrides` is the state's overrides file, keyed by beach id.
    """
    statuses = read_statuses(raw_dir) if raw_dir.is_dir() else {}
    ran = _ran(statuses)
    stats: Counter = Counter()
    geo, report = _geometry(raw_dir, run_date, ran, cached, light, stats, overrides)

    records = []
    for rid in sorted(geo):
        blk = geo[rid]
        shp = blk["data"]["shape"]
        rec = dict(blk["data"]["core"])
        b, g = shp.get("boundary"), shp.get("grid")
        w = shp.get("water") or {}
        rec.update(area_m2=shp.get("area_m2"), has_boundary=b is not None,
                   boundary_points=b["point_count"] if b else None, has_grid=g is not None,
                   cell_count=g["cell_count"] if g else None, water_kind=w.get("kind"),
                   water_edge_m=w.get("edge_m"), orientation=w.get("orientation"))
        rec["_point"] = normalize.lookup_point(rec, b)
        if keep is not None:
            where = keep(rec)
            if where is None:
                report["other_state"] += 1
                continue
            rec["state"], rec["country"], rec["state_confidence"] = where
        rec["_shape"] = shp
        rec["tiles"] = blk["data"]["tiles"]
        rec["_blocks"] = {"geometry": blk}
        for block in BLOCKS[1:]:
            for f in block.fields:
                rec[f] = None
        records.append(rec)
    by_id = {r["id"]: r for r in records}
    old_block = lambda rid, name: cached.get(rid, {}).get(name)

    # nearby: tile based; every beach whose tiles were re-queried gets a fresh list
    nearby_lists: dict[str, list] = {}
    if not light:
        fresh_lists: dict[str, list] = {}
        covered: set[str] = set()
        if (raw_dir / "osm_nearby").is_dir() and any((raw_dir / "osm_nearby").glob("*.json")):
            ran.add("nearby")
            payloads = overpass.load_tiles(raw_dir, "osm_nearby")
            pulled = {p["tile"]["id"] for p in payloads}
            items: dict[tuple, dict] = {}
            for p in payloads:
                for el in p["response"]["elements"]:
                    it = nearbylib.item_record(el)
                    if it:
                        items.setdefault((it["osm_type"], it["osm_id"]), it)
            report["nearby_items"] = len(items)
            covered = {r["id"] for r in records if pulled & set(geo[r["id"]]["data"]["tiles"])}
            shapes = {rid: _attach_shape(by_id[rid], by_id[rid]["_shape"]) for rid in covered}
            fresh_lists = nearbylib.attach(shapes, list(items.values()))
        for rec in records:
            rid = rec["id"]
            old = old_block(rid, "nearby")
            if rid in covered:
                found = fresh_lists.get(rid, [])
                h = nearbylib.nearby_hash(found)
                stats["nearby_unchanged" if old and old["hash"] == h else "nearby_changed"] += 1
                blk = {"hash": h, "pulled_at": run_date, "data": {"items": found}}
            elif old:
                blk = old
                stats["nearby_kept"] += 1
            else:
                blk = None
            rec["_blocks"]["nearby"] = blk
            found = blk["data"]["items"] if blk else None
            if found:
                nearby_lists[rid] = found
            if blk:
                counts = nearbylib.counts(found)
                rec.update(nearby_counts=counts, has_webcam=counts.get("webcam", 0) > 0,
                           has_surf_spot=counts.get("surf_spot", 0) > 0)

    # source blocks, in table order; sources inside a block in their listed order
    regions = {t.id: t.region for t in TILES}
    for rec in records:
        rec["_regions"] = {regions.get(t, "inland") for t in rec["tiles"]}
    for block in BLOCKS[2:]:
        if block.name == "terrain":
            continue  # below: heights travel with the fields
        any_fresh: dict[str, bool] = {}
        for src in block.sources:
            module = MODULES[src]
            status = statuses.get(src)
            fresh = module.fresh(raw_dir, records) if status and not status.get("skipped") else None
            for rec in records:
                old = old_block(rec["id"], block.name)
                got = fresh.get(rec["id"]) if fresh else None
                if got is not None:
                    rec.update({k: got.get(k) for k in module.FIELDS})
                    any_fresh[rec["id"]] = True
                elif old:
                    rec.update({k: old["data"]["fields"].get(k) for k in module.FIELDS})
        for rec in records:
            rid, old = rec["id"], old_block(rec["id"], block.name)
            fields = {k: rec[k] for k in block.fields}
            if any_fresh.get(rid):
                h = sha256(fields)
                stats[f"{block.name}_unchanged" if old and old["hash"] == h
                      else f"{block.name}_changed"] += 1
                rec["_blocks"][block.name] = {"hash": h, "pulled_at": run_date,
                                              "data": {"fields": fields}}
            elif old:
                stats[f"{block.name}_kept"] += 1
                rec["_blocks"][block.name] = old
            else:
                rec["_blocks"][block.name] = None

    if not light:
        _terrain(raw_dir, run_date, records, statuses, cached, stats)

    boundaries, grids = {}, {}
    stats_cells: Counter = Counter()
    for i, rec in enumerate(records):
        ov = (overrides or {}).get(rec["id"])
        overrideslib.apply_name(rec, ov)  # a corrected name counts in the score
        records[i] = rec = with_display_name(rec)
        rec["score"] = score(rec)
        rec["tier"] = tier(rec, rec["score"])
        rec["seed_space"] = rec["tier"] == "primary"
        overrideslib.apply_tier(rec, ov)  # a corrected tier wins
        rec["last_pulled"] = rec["_blocks"]["geometry"]["pulled_at"]
        rec["blocks"] = {name: ({"hash": blk["hash"], "pulled_at": blk["pulled_at"],
                                 **({"grid_key": blk["data"]["grid_key"]}
                                    if name == "terrain" else {})} if blk else None)
                         for name, blk in rec["_blocks"].items()}
        shp = rec["_shape"]
        if shp.get("boundary"):
            boundaries[rec["id"]] = shp["boundary"]
        if shp.get("grid"):
            recs, numbers = cells.records(shp["grid"], rec, rec.get("_heights_cm"))
            rec.update(numbers)
            grids[rec["id"]] = cells.pack_grid(shp["grid"], recs)
            for r in recs:
                stats_cells[r[2]] += 1
        else:
            rec.update(datum_offset_m=None, wet_limit_m=None, tide_reference=None)
        rec["tags"] = rec.pop("tags")  # keep tags last
    records.sort(key=normalize.sort_key)
    names = {cells.WASH: "wash", cells.WET: "wet", cells.DRY: "dry", cells.BACK: "back",
             cells.UNKNOWN: "unknown"}
    report.update({f"cells_{names[k]}": v for k, v in stats_cells.items()})
    extras = {"boundaries": boundaries, "grids": grids, "nearby": nearby_lists,
              "statuses": statuses, "ran": ran,
              "overrides": {"applied": sum(1 for r in records if r.get("override")),
                            "unmatched": sorted(set(overrides or {}) - {r["id"] for r in records})}}
    return records, extras, dict(stats), dict(report)


def _terrain(raw_dir: Path, run_date: str, records: list[dict], statuses: dict, cached: dict,
             stats: Counter) -> None:
    """The terrain block: fields plus one height per cell. Fresh when this
    run measured the beach's current grid (or kept the raster to measure it
    again); else carried from the previous pack when it was taken for the
    same grid; else none."""
    from .sources import terrain as terrainlib
    fresh = terrainlib.load(raw_dir) if statuses.get("terrain") else {}
    keys = {r["id"]: terrainlib.grid_key(r["_shape"]["grid"]) for r in records
            if r["_shape"].get("grid")}
    stale = [r for r in records if r["id"] in fresh and fresh[r["id"]].get("grid_key")
             != keys.get(r["id"])]
    for rid, got in terrainlib.resample(raw_dir, stale).items():
        fresh[rid] = got  # the grid changed since the pull: measured again from the rasters
        stats["terrain_remeasured"] += 1
    for rec in records:
        rid, key = rec["id"], keys.get(rec["id"])
        old = cached.get(rid, {}).get("terrain")
        if old and old["data"].get("grid_key") != key:
            old = None  # heights of another grid
        got = fresh.get(rid)
        if got and key and got.get("grid_key") == key:
            fields, heights = got["fields"], got["heights_cm"]
            h = sha256([fields, heights])
            stats["terrain_unchanged" if old and old["hash"] == h else "terrain_changed"] += 1
            blk = {"hash": h, "pulled_at": run_date,
                   "data": {"fields": fields, "grid_key": key, "heights_cm": heights}}
        elif old:
            stats["terrain_kept"] += 1
            blk = old
        else:
            blk = None
        rec["_blocks"]["terrain"] = blk
        rec.update(blk["data"]["fields"] if blk else {})
        rec["_heights_cm"] = blk["data"].get("heights_cm") if blk else None


UNNAMED = "Unnamed stretch"


def display_name(rec: dict) -> str:
    """The OSM name, else "Unnamed stretch near <nearest Census place>"."""
    if rec.get("name"):
        return rec["name"]
    place = rec.get("nearest_place")
    return f"{UNNAMED} near {place}" if place else UNNAMED


def with_display_name(rec: dict) -> dict:
    """The record with display_name placed right after name."""
    out = {}
    for k, v in rec.items():
        if k == "display_name":
            continue
        out[k] = v
        if k == "name":
            out["display_name"] = display_name(rec)
    return out


def public(rec: dict) -> dict:
    """The record as written to beaches.json (internal keys dropped)."""
    return {k: v for k, v in rec.items() if not k.startswith("_")}
