"""python3 -m beachx_pack <pull|build|run|dist|diff|export|stats|show> --state XX"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

from dataclasses import dataclass

from . import blocks, diffpack, export, overpass, previous, states, stats
from .assemble import assemble
from .pipeline import build_pack
from .sources import MODULES
from .tiles import COUNTRY_OF_STATE, STATE_BOXES, TILES, select_tiles, tiles_for_state

ROOT = Path(__file__).resolve().parent.parent
RAW_ROOT = ROOT / "raw"
PACK_ROOT = ROOT / "pack"
OVERRIDES = ROOT / "overrides"
ALL_SOURCES = tuple(overpass.KIND_BLOCK) + tuple(MODULES)
COUNTRY = "us"  # the folder every US state and territory pack lives under


@dataclass
class Where:
    """One state's folders: raw/<st>/<run-date>/ and pack/us/<st>/."""
    state: str

    @property
    def code(self) -> str:
        return self.state.upper()

    @property
    def raw(self) -> Path:
        return RAW_ROOT / self.state.lower()

    @property
    def pack(self) -> Path:
        return PACK_ROOT / COUNTRY / self.state.lower()

    @property
    def overrides_path(self) -> Path:
        return OVERRIDES / f"{self.state.lower()}.json"

    def overrides(self) -> dict:
        from .overrides import load
        return load(self.overrides_path)


def where(args: argparse.Namespace) -> Where:
    code = (args.state or "").strip().upper()
    if not code:
        raise ValueError("--state is required (a USPS code such as HI)")
    if code not in STATE_BOXES:
        raise ValueError(f"unknown state: {code}")
    return Where(code)


def keeper(code: str):
    """keep(rec) for assemble: (state, country, confidence) when the beach's
    lookup point is in the state's TIGER polygon (or within 2 km of it and in
    no other state), else None."""
    every = states.load()
    target = every[code]
    country = COUNTRY_OF_STATE.get(code, "US")

    def keep(rec: dict):
        conf = states.assign(*rec["_point"], target, every)
        return (code, country, conf) if conf else None
    return keep


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _latest_run(w: Where) -> str | None:
    dates = sorted(p.name for p in w.raw.glob("????-??-??") if (p / "_status").is_dir())
    return dates[-1] if dates else None


QUEUES = ("overpass", "sources")


def pull_overpass(args: argparse.Namespace, raw_dir: Path, chosen: list[str],
                  only: set[str]) -> bool:
    """The Overpass queue: geometry and nearby tiles, one atomic file per tile.
    Returns True when every selected tile is on disk."""
    kinds = tuple(k for k, b in overpass.KIND_BLOCK.items() if b in chosen and k in only)
    if not kinds:
        print("overpass queue: nothing selected")
        return True
    tiles = args._tiles
    print(f"overpass queue: {len(tiles)} tiles, queries {', '.join(kinds)}")
    ok = True
    for kind, st in overpass.pull_tiles(tiles, raw_dir, kinds=kinds, force=args.force,
                                        endpoints=args.endpoint or None).items():
        blocks.write_status(raw_dir, kind, st)
        ok &= not st["failed_tiles"]
        print(f"overpass {kind}: {st['calls']} calls, {st['cached']} cached, "
              f"{st['errors']} failed, {st['seconds']}s")
    if not ok:
        print("some tiles failed; run the same command again to resume")
    return ok


def pull_sources(args: argparse.Namespace, raw_dir: Path, chosen: list[str],
                 only: set[str], w: Where) -> None:
    """The sources queue: every non-Overpass source, working from the tiles
    already on disk (this run's, else the previous pack's geometry)."""
    opts = {"force": args.force, "limit": args.limit, "wikidata_limit": args.wikidata_limit,
            "wikipedia_limit": args.wikipedia_limit}
    records = None
    keep = keeper(w.code)
    overrides = w.overrides()
    for block in blocks.BLOCKS[2:]:
        if block.name not in chosen:
            continue
        for src in block.sources:
            if src not in only:
                continue
            if records is None or src == "wikipedia":  # rank with the latest data
                records, *_ = assemble(raw_dir, run_date_of(raw_dir), previous.load(w.pack)[0],
                                       light=True, keep=keep, overrides=overrides)
                print(f"ranked {len(records)} beaches: "
                      f"{sum(r['tier'] == 'primary' for r in records)} primary, "
                      f"{sum(r['tier'] == 'secondary' for r in records)} secondary")
            print(f"{block.name}/{src}:")
            if src == "terrain":  # needs the grids, and the heights already in the pack
                cached = previous.load(w.pack)[0]
                opts["previous"] = cached
                records, *_ = assemble(raw_dir, run_date_of(raw_dir), cached, keep=keep,
                                       overrides=overrides)
            started = time.time()
            try:
                status = MODULES[src].pull(raw_dir, records, opts)
            except Exception as err:  # a source must never stop the pull
                status = {"calls": 0, "ok": 0, "errors": 1, "cached": 0, "endpoint": None,
                          "note": f"skipped: {type(err).__name__}: {err}"}
                print(f"  {src}: {status['note']}")
            status.setdefault("seconds", round(time.time() - started, 1))
            blocks.write_status(raw_dir, src, status)


def run_date_of(raw_dir: Path) -> str:
    return raw_dir.name


def cmd_pull(args: argparse.Namespace) -> int:
    """`pull --queue overpass`, `pull --queue sources`, or both in sequence.
    The two queues can also run at the same time in two shells."""
    w = where(args)
    run_date = args.run_date or _today()
    raw_dir = w.raw / run_date
    prev = export.load_manifest(w.pack)
    if args.tiles:
        args._tiles = select_tiles(args.tiles)
    else:
        box = states.load({w.code})[w.code].bbox if not any(t.state == w.code for t in TILES) \
            else None
        args._tiles = tiles_for_state(w.code, box)
    chosen = blocks.choose(args.blocks, args.skip_blocks, args.due, prev,
                           date.fromisoformat(run_date))
    only = set(blocks.parse_list(args.sources, ALL_SOURCES, "source")) or set(ALL_SOURCES)
    only -= set(blocks.parse_list(args.skip_sources, ALL_SOURCES, "source"))
    queues = QUEUES if args.queue in (None, "both") else (args.queue,)
    print(f"pull {w.code} {run_date} ({' + '.join(queues)}): blocks "
          f"{', '.join(chosen) or '(none due)'}"
          f" -> {raw_dir.relative_to(ROOT)}")
    ok = True
    if "overpass" in queues:
        ok = pull_overpass(args, raw_dir, chosen, only)
    if "sources" in queues:
        pull_sources(args, raw_dir, chosen, only, w)
    return 0 if ok else 1


def cmd_build(args: argparse.Namespace) -> int:
    w = where(args)
    run_date = args.run_date or _latest_run(w)
    if not run_date:
        print(f"no raw pull found in {w.raw.relative_to(ROOT)}; run `python3 -m beachx_pack pull "
              f"--state {w.code}` first")
        return 1
    started = time.time()
    overrides = w.overrides()
    man, changes = build_pack(w.raw / run_date, w.pack, run_date, keep=keeper(w.code),
                              overrides=overrides, state=w.code,
                              country=COUNTRY_OF_STATE.get(w.code, "US"))
    export.update_index(PACK_ROOT, man)
    c = man["counts"]
    print(f"build {w.code} {run_date}: {c['beaches']} beaches -> {w.pack.relative_to(ROOT)}/  "
          f"({time.time() - started:.1f}s)")
    print(f"  tiers {c['by_tier']}  boundaries {c['with_boundary']}  grids {c['with_grid']}  "
          f"cells {c['grid_cells']}")
    print(f"  pack_hash {man['pack_hash'][:16]}")
    for name, b in man["blocks"].items():
        ch = {k.split("_", 1)[1]: v for k, v in changes.items() if k.startswith(name + "_")}
        print(f"  {name:<9} {b['status']:<14} last_run {b['last_run'] or '-':<10} "
              f"beaches {b['beaches']:>6}  {ch}")
    print(f"  geometry pipeline {c['geometry_pipeline']}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    args.run_date = args.run_date or _today()
    if cmd_pull(args):
        print("pull incomplete; not building. Re-run to resume.")
        return 1
    return cmd_build(args)


def cmd_diff(args: argparse.Namespace) -> int:
    w = where(args)
    current, cur_nearby = diffpack.from_path(w.pack / "beaches.json")
    if args.against:
        previous, prev_nearby = diffpack.from_path(Path(args.against))
        source = args.against
    else:
        head = diffpack.from_git_head(ROOT, str(w.pack.relative_to(ROOT)), w.code)
        if head is None:
            print("no previous pack: git HEAD has no pack/beaches.json")
            return 0
        previous, prev_nearby = head
        source = "git HEAD"
    print(f"diff {source} ({len(previous)}) -> {w.pack.relative_to(ROOT)}/beaches.json "
          f"({len(current)})")
    print(diffpack.render(diffpack.compare(previous, current, prev_nearby, cur_nearby)))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    if not args.dart and not args.json:
        print("export needs --dart PATH or --json PATH")
        return 2
    w = where(args)
    records = export.load_pack(w.pack)
    run_date = export.load_manifest(w.pack)["run_date"]
    chosen = export.select(records, args.tier)
    if args.dart:
        named = [r for r in chosen if r["name"]]
        Path(args.dart).parent.mkdir(parents=True, exist_ok=True)
        Path(args.dart).write_text(export.to_dart(named, run_date, args.class_name))
        print(f"wrote {args.dart}: {len(named)} {args.tier} beaches "
              f"({len(chosen) - len(named)} unnamed skipped)")
    if args.json:
        data = export.to_app_json(chosen, w.pack, boundaries=args.boundaries, grids=args.grids,
                                  nearby=args.nearby, run_date=run_date)
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        print(f"wrote {args.json}: {len(chosen)} {args.tier} beaches")
    return 0


def cmd_dist(args: argparse.Namespace) -> int:
    from . import dist
    w = where(args)
    if not (w.pack / "manifest.json").exists():
        print(f"no pack in {w.pack.relative_to(ROOT)}; run build --state {w.code} first")
        return 1
    started = time.time()
    entry = dist.build(w.pack, ROOT / "dist")
    print(f"dist {w.code}: dist/{entry['path']}  {entry['size'] / 1e6:.2f} MB gzipped "
          f"({entry['sqlite_size'] / 1e6:.2f} MB SQLite), {entry['beaches']} beaches, "
          f"{entry['grids']} grids, {entry['cells']} cells, sha256 {entry['sha256'][:16]}  "
          f"({time.time() - started:.1f}s)")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """A beach's record, boundary, and grid with its rows decoded, for review."""
    from . import cells
    w = where(args)
    rec = next((r for r in export.load_pack(w.pack) if r["id"] == args.beach_id), None)
    if rec is None:
        print(f"no beach {args.beach_id} in {w.pack.relative_to(ROOT)}")
        return 1
    grid = export.load_shapes(w.pack, "grids").get(args.beach_id)
    out = {"beach": {k: v for k, v in rec.items() if k != "tags"},
           "boundary": export.load_shapes(w.pack, "boundaries").get(args.beach_id)}
    if grid:
        runs, recs = cells.decode(cells.unb64(grid["rows"]))
        rows, k = [], 0
        for y, x0, n in runs:
            rows.append({"y": y, "x0": x0, "cells": [list(r) for r in recs[k:k + n]]})
            k += n
        out["grid"] = {**{key: v for key, v in grid.items() if key != "rows"},
                       "cell": ["height", "water", "volatility", "slope", "flags"], "rows": rows}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    w = where(args)
    print(stats.report(export.load_pack(w.pack), top=args.top,
                       manifest=export.load_manifest(w.pack), today=date.today()))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python3 -m beachx_pack", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    def state_arg(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--state", required=True,
                        help="the state to work on, a USPS code such as HI or KS")

    def pull_args(sp: argparse.ArgumentParser) -> None:
        state_arg(sp)
        sp.add_argument("--tiles",
                        help="default: the state's tiles. Or a region (hawaii, alaska, atlantic, "
                             "gulf, pacific, great_lakes, territories), or a comma list of "
                             "regions and state codes")
        sp.add_argument("--run-date", help="YYYY-MM-DD folder under raw/ (default: today, UTC)")
        sp.add_argument("--force", action="store_true", help="re-fetch what is already cached")
        sp.add_argument("--blocks", help=f"only these blocks ({', '.join(blocks.NAMES)})")
        sp.add_argument("--skip-blocks", help="all blocks but these")
        sp.add_argument("--due", action="store_true",
                        help="only blocks whose cadence has elapsed since their last_run")
        sp.add_argument("--sources", help=f"only these sources ({', '.join(ALL_SOURCES)})")
        sp.add_argument("--skip-sources", help="all sources but these")
        sp.add_argument("--limit", type=int, help="cap per-point calls per source (try 10)")
        sp.add_argument("--wikidata-limit", type=int, default=5000)
        sp.add_argument("--wikipedia-limit", type=int, default=1000)
        sp.add_argument("--endpoint", action="append",
                        help="Overpass endpoint to use instead of the defaults (repeatable)")
        sp.add_argument("--queue", choices=("overpass", "sources", "both"), default="both",
                        help="overpass: geometry and nearby tiles; sources: everything else, "
                             "from the tiles on disk; both (default): overpass, then sources")

    pull_args(sub.add_parser("pull", help="fetch blocks into raw/<run-date>/"))
    pull_args(sub.add_parser("run", help="pull, then build"))
    sp = sub.add_parser("build", help="raw/<st>/<run-date>/ + the previous pack -> pack/us/<st>/")
    state_arg(sp)
    sp.add_argument("--run-date", help="default: latest folder in raw/<st>/")
    sp = sub.add_parser("diff", help="compare the state pack with a previous one, block by block")
    state_arg(sp)
    sp.add_argument("--against", help="previous beaches.json (default: git HEAD)")
    sp = sub.add_parser("export", help="app exports")
    state_arg(sp)
    sp.add_argument("--dart", help="write a Dart seed class here")
    sp.add_argument("--json", help="write a trimmed JSON asset here")
    sp.add_argument("--tier", default="primary", choices=["primary", "secondary", "minor", "all"])
    sp.add_argument("--class-name", default="PackBeaches")
    sp.add_argument("--boundaries", action="store_true", help="JSON: include boundary rings")
    sp.add_argument("--grids", action="store_true", help="JSON: include official grids")
    sp.add_argument("--nearby", action="store_true", help="JSON: include nearby items")
    sp = sub.add_parser("dist", help="pack/us/<st>/ -> dist/us/<st>.sqlite.gz and dist/index.json")
    state_arg(sp)
    sp = sub.add_parser("show", help="one beach's record and decoded grid rows, as JSON")
    state_arg(sp)
    sp.add_argument("beach_id")
    sp = sub.add_parser("stats", help="block table, counts, top beaches, largest grids")
    state_arg(sp)
    sp.add_argument("--top", type=int, default=25)
    return p


COMMANDS = {"pull": cmd_pull, "build": cmd_build, "run": cmd_run, "diff": cmd_diff,
            "export": cmd_export, "stats": cmd_stats, "show": cmd_show, "dist": cmd_dist}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
