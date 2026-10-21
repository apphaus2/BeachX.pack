"""Named data blocks, each with its own cadence, sources, and output fields.

A pull runs or skips whole blocks. A build always rebuilds every record: a
block pulled in this run comes from raw/<run-date>/, any other block comes
unchanged from the previous pack with its old pulled_at, and a block that was never
pulled is null.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class Block:
    name: str
    cadence_days: int
    sources: tuple[str, ...]  # modules under beachx_pack/sources/ (or the OSM pulls)
    per_point: bool  # True: one request per beach, primary and secondary only
    fields: tuple[str, ...]  # record fields this block owns (beyond geometry)
    what: str


BLOCKS: tuple[Block, ...] = (
    Block("geometry", 365, ("osm",), False, (),
          "OSM beach tags and geometry: boundary, area, score, tier, grid"),
    Block("nearby", 90, ("osm_nearby",), False,
          ("nearby_counts", "has_webcam", "has_surf_spot"),
          "webcams, surf spots, surf shops, lifeguard posts, parking, toilets, showers, "
          "drinking water within 300 m"),
    Block("stations", 365, ("tides", "buoys"), False,
          ("tide_station_id", "tide_station_name", "tide_station_distance_km",
           "wave_buoy_id", "wave_buoy_name", "wave_buoy_distance_km"),
          "nearest NOAA CO-OPS tide station and NDBC buoy, from two downloads"),
    Block("places", 730, ("census", "timezone"), True,
          ("county", "county_fips", "place", "place_fips", "census_state", "timezone"),
          "Census county and place (per point), time zone (table, every tier)"),
    Block("land", 365, ("padus",), True,
          ("public_access", "land_manager_type", "land_manager_name", "designation",
           "padus_unit_name"),
          "PAD-US public access, manager, designation"),
    Block("weather", 365, ("nws",), True,
          ("nws_forecast_zone", "nws_county_zone", "nws_office", "nws_grid"),
          "NWS forecast zone, county zone, office, grid"),
    Block("wiki", 90, ("wikidata", "wikipedia"), True,
          ("wikidata_label", "wikidata_sitelinks", "wikidata_is_beach", "wikidata_is_park",
           "description", "description_license", "description_source_url"),
          "Wikidata sitelinks and facts, Wikipedia first paragraph (primary only)"),
)
BY_NAME = {b.name: b for b in BLOCKS}
NAMES = tuple(b.name for b in BLOCKS)
SOURCE_BLOCK = {src: b.name for b in BLOCKS for src in b.sources}


def parse_list(text: str | None, allowed: tuple[str, ...], what: str) -> list[str]:
    if not text:
        return []
    items = [p.strip() for p in text.split(",") if p.strip()]
    bad = [p for p in items if p not in allowed]
    if bad:
        raise ValueError(f"unknown {what}: {', '.join(bad)} (known: {', '.join(allowed)})")
    return items


def is_due(name: str, manifest: dict | None, today: date) -> bool:
    """Due when the block's cadence has elapsed since its last_run. Every block
    is due when there is no pack yet."""
    info = ((manifest or {}).get("blocks") or {}).get(name) or {}
    last = info.get("last_run")
    if not last:
        return True
    return (today - date.fromisoformat(last)).days >= BY_NAME[name].cadence_days


def choose(blocks: str | None, skip: str | None, due: bool, manifest: dict | None,
           today: date) -> list[str]:
    chosen = parse_list(blocks, NAMES, "block") or list(NAMES)
    skipped = set(parse_list(skip, NAMES, "block"))
    chosen = [b for b in NAMES if b in chosen and b not in skipped]
    if due:
        chosen = [b for b in chosen if is_due(b, manifest, today)]
    return chosen


def status_dir(raw_dir: Path) -> Path:
    return raw_dir / "_status"


def write_status(raw_dir: Path, source: str, status: dict) -> None:
    folder = status_dir(raw_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{source}.json"
    tmp = path.with_suffix(f".json.{os.getpid()}.tmp")  # atomic: the queues run side by side
    tmp.write_text(json.dumps({"source": source, "block": SOURCE_BLOCK[source], **status},
                              indent=1) + "\n")
    tmp.replace(path)


def read_statuses(raw_dir: Path) -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text())
            for p in sorted(status_dir(raw_dir).glob("*.json"))}


def ran_blocks(raw_dir: Path) -> set[str]:
    """Blocks with at least one source status file in this run's raw folder."""
    return {s["block"] for s in read_statuses(raw_dir).values()}
