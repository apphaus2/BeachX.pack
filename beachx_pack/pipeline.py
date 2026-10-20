"""build: raw/<run-date>/ + the previous pack/ -> the new pack/."""

from __future__ import annotations

from pathlib import Path

from . import export, previous
from .assemble import assemble, public


def build_pack(raw_dir: Path, pack_dir: Path, run_date: str) -> tuple[dict, dict]:
    """Read the previous pack, merge this run's raw blocks over it, write the new
    pack. Returns (manifest, block change counts)."""
    cached, prev = previous.load(pack_dir)
    records, extras, block_stats, report = assemble(raw_dir, run_date, cached)
    rows = [public(r) for r in records]
    tile_ids = sorted({t for r in rows for t in r["tiles"]})
    man = export.manifest(rows, extras, run_date, report, block_stats, prev, tile_ids)
    export.write_pack(pack_dir, rows, extras, man)
    return man, block_stats
