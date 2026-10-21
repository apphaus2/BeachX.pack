"""build: raw/<state>/<run-date>/ + the previous pack/<country>/<state>/ -> the new one."""

from __future__ import annotations

from pathlib import Path

from . import export, previous
from .assemble import assemble, public


def build_pack(raw_dir: Path, pack_dir: Path, run_date: str, *, keep=None,
               overrides: dict | None = None, state: str | None = None,
               country: str = "US") -> tuple[dict, dict]:
    """Read the previous pack, merge this run's raw blocks over it, write the new
    pack. Returns (manifest, block change counts). `keep` and `overrides` are
    assemble's; `state` and `country` label the manifest."""
    cached, prev = previous.load(pack_dir)
    records, extras, block_stats, report = assemble(raw_dir, run_date, cached, keep=keep,
                                                    overrides=overrides)
    rows = [public(r) for r in records]
    tile_ids = sorted({t for r in rows for t in r["tiles"]})
    man = export.manifest(rows, extras, run_date, report, block_stats, prev, tile_ids,
                          state=state, country=country)
    export.write_pack(pack_dir, rows, extras, man)
    return man, block_stats
