"""python3 -m beachx_pack <pull|build|run|diff|export|stats>"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

from . import blocks, diffpack, export, overpass, previous, stats
from .assemble import assemble
from .pipeline import build_pack
from .sources import MODULES
from .tiles import select_tiles

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
PACK = ROOT / "pack"
ALL_SOURCES = tuple(overpass.KIND_BLOCK) + tuple(MODULES)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _latest_run() -> str | None:
    dates = sorted(p.name for p in RAW.glob("????-??-??") if (p / "_status").is_dir())
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
    tiles = select_tiles(args.tiles)
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
                 only: set[str]) -> None:
    """The sources queue: every non-Overpass source, working from the tiles
    already on disk (this run's, else the previous pack's geometry)."""
    opts = {"force": args.force, "limit": args.limit, "wikidata_limit": args.wikidata_limit,
            "wikipedia_limit": args.wikipedia_limit}
    records = None
    for block in blocks.BLOCKS[2:]:
        if block.name not in chosen:
            continue
        for src in block.sources:
            if src not in only:
                continue
            if records is None or src == "wikipedia":  # rank with the latest data
                records, *_ = assemble(raw_dir, run_date_of(raw_dir), previous.load(PACK)[0],
                                       light=True)
                print(f"ranked {len(records)} beaches: "
                      f"{sum(r['tier'] == 'primary' for r in records)} primary, "
                      f"{sum(r['tier'] == 'secondary' for r in records)} secondary")
            print(f"{block.name}/{src}:")
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
    run_date = args.run_date or _today()
    raw_dir = RAW / run_date
    prev = export.load_manifest(PACK)
    chosen = blocks.choose(args.blocks, args.skip_blocks, args.due, prev,
                           date.fromisoformat(run_date))
    only = set(blocks.parse_list(args.sources, ALL_SOURCES, "source")) or set(ALL_SOURCES)
    only -= set(blocks.parse_list(args.skip_sources, ALL_SOURCES, "source"))
    queues = QUEUES if args.queue in (None, "both") else (args.queue,)
    print(f"pull {run_date} ({' + '.join(queues)}): blocks {', '.join(chosen) or '(none due)'}"
          f" -> {raw_dir.relative_to(ROOT)}")
    ok = True
    if "overpass" in queues:
        ok = pull_overpass(args, raw_dir, chosen, only)
    if "sources" in queues:
        pull_sources(args, raw_dir, chosen, only)
    return 0 if ok else 1


def cmd_build(args: argparse.Namespace) -> int:
    run_date = args.run_date or _latest_run()
    if not run_date:
        print("no raw pull found; run `python3 -m beachx_pack pull` first")
        return 1
    started = time.time()
    man, changes = build_pack(RAW / run_date, PACK, run_date)
    c = man["counts"]
    print(f"build {run_date}: {c['beaches']} beaches -> pack/  ({time.time() - started:.1f}s)")
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
    current, cur_nearby = diffpack.from_path(PACK / "beaches.json")
    if args.against:
        previous, prev_nearby = diffpack.from_path(Path(args.against))
        source = args.against
    else:
        head = diffpack.from_git_head(ROOT)
        if head is None:
            print("no previous pack: git HEAD has no pack/beaches.json")
            return 0
        previous, prev_nearby = head
        source = "git HEAD:pack/beaches.json"
    print(f"diff {source} ({len(previous)}) -> pack/beaches.json ({len(current)})")
    print(diffpack.render(diffpack.compare(previous, current, prev_nearby, cur_nearby)))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    if not args.dart and not args.json:
        print("export needs --dart PATH or --json PATH")
        return 2
    records = export.load_pack(PACK)
    run_date = export.load_manifest(PACK)["run_date"]
    chosen = export.select(records, args.tier)
    if args.dart:
        named = [r for r in chosen if r["name"]]
        Path(args.dart).parent.mkdir(parents=True, exist_ok=True)
        Path(args.dart).write_text(export.to_dart(named, run_date, args.class_name))
        print(f"wrote {args.dart}: {len(named)} {args.tier} beaches "
              f"({len(chosen) - len(named)} unnamed skipped)")
    if args.json:
        data = export.to_app_json(chosen, PACK, boundaries=args.boundaries, grids=args.grids,
                                  nearby=args.nearby, run_date=run_date)
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        print(f"wrote {args.json}: {len(chosen)} {args.tier} beaches")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    print(stats.report(export.load_pack(PACK), top=args.top,
                       manifest=export.load_manifest(PACK), today=date.today()))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python3 -m beachx_pack", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    def pull_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--tiles", default="all",
                        help="all, a region (hawaii, alaska, atlantic, gulf, pacific, "
                             "great_lakes, territories), or a comma list of regions/state codes")
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
    sp = sub.add_parser("build", help="raw/<run-date>/ + the previous pack/ -> pack/")
    sp.add_argument("--run-date", help="default: latest folder in raw/")
    sp = sub.add_parser("diff", help="compare pack/ with a previous pack, block by block")
    sp.add_argument("--against", help="previous beaches.json (default: git HEAD)")
    sp = sub.add_parser("export", help="app exports")
    sp.add_argument("--dart", help="write a Dart seed class here")
    sp.add_argument("--json", help="write a trimmed JSON asset here")
    sp.add_argument("--tier", default="primary", choices=["primary", "secondary", "minor", "all"])
    sp.add_argument("--class-name", default="PackBeaches")
    sp.add_argument("--boundaries", action="store_true", help="JSON: include boundary rings")
    sp.add_argument("--grids", action="store_true", help="JSON: include official grids")
    sp.add_argument("--nearby", action="store_true", help="JSON: include nearby items")
    sp = sub.add_parser("stats", help="block table, counts, top beaches, largest grids")
    sp.add_argument("--top", type=int, default=25)
    return p


COMMANDS = {"pull": cmd_pull, "build": cmd_build, "run": cmd_run, "diff": cmd_diff,
            "export": cmd_export, "stats": cmd_stats}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
