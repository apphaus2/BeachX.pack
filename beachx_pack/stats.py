"""Summary counts for review."""

from __future__ import annotations

from collections import Counter
from datetime import date

from .blocks import BLOCKS, is_due
from .score import TIERS


def _table(title: str, counter: Counter, limit: int | None = None) -> list[str]:
    lines = [title]
    for key, n in counter.most_common(limit):
        lines.append(f"  {str(key):<28} {n:7d}")
    return lines


def block_table(manifest: dict | None, today: date) -> list[str]:
    lines = ["blocks:", f"  {'block':<10} {'cadence':>7}  {'last run':<10}  {'due':<3}  "
             f"{'beaches':>7}  status"]
    info = (manifest or {}).get("blocks") or {}
    for block in BLOCKS:
        b = info.get(block.name) or {}
        lines.append(f"  {block.name:<10} {block.cadence_days:>6}d  {b.get('last_run') or '-':<10}  "
                     f"{'yes' if is_due(block.name, manifest, today) else 'no':<3}  "
                     f"{b.get('beaches', 0):>7}  {b.get('status', 'never run')}")
        for src, st in (b.get("sources") or {}).items():
            lines.append(f"    {src:<10} calls {st.get('calls', 0):>4}  ok {st.get('ok', 0):>4}  "
                         f"errors {st.get('errors', 0):>3}  cached {st.get('cached', 0):>4}  "
                         f"{st.get('seconds', 0):>6}s"
                         + (f"  note: {st['note']}" if st.get("note") else ""))
    return lines


def report(records: list[dict], top: int = 25, manifest: dict | None = None,
           today: date | None = None) -> str:
    lines = [f"beaches: {len(records)}"]
    lines += block_table(manifest, today or date.today())
    lines += _table("by tier:", Counter({t: sum(r["tier"] == t for r in records) for t in TIERS}))
    lines += _table("by state:", Counter(r["state"] or "unknown" for r in records))
    lines += _table("by state_confidence:", Counter(r["state_confidence"] or "none" for r in records))
    lines += _table("by surface (top 15):", Counter(r["surface"] or "(none)" for r in records), 15)
    lines.append("coverage:")
    for label, test in [
        ("named", lambda r: bool(r["name"])),
        ("wikipedia tag", lambda r: bool(r["wikipedia"])),
        ("wikidata tag", lambda r: bool(r["wikidata"])),
        ("lifeguard tag", lambda r: r["lifeguard"] is not None),
        ("lifeguard = yes", lambda r: r["lifeguard"] == "yes"),
        ("has_boundary", lambda r: r["has_boundary"]),
        ("has_grid", lambda r: r["has_grid"]),
        ("has_webcam", lambda r: bool(r.get("has_webcam"))),
        ("has_surf_spot", lambda r: bool(r.get("has_surf_spot"))),
        ("tide station", lambda r: r.get("tide_station_id") is not None),
        ("census county", lambda r: r.get("county") is not None),
        ("PAD-US unit", lambda r: r.get("padus_unit_name") is not None),
        ("NWS zone", lambda r: r.get("nws_forecast_zone") is not None),
        ("description", lambda r: r.get("description") is not None),
        ("seed_space", lambda r: r["seed_space"]),
    ]:
        lines.append(f"  {label:<28} {sum(1 for r in records if test(r)):7d}")
    lines.append(f"  {'grid cells (total)':<28} {sum(r['cell_count'] or 0 for r in records):7d}")
    lines.append(f"top {top} by score:")
    ranked = sorted(records, key=lambda r: (-r["score"], -(r["area_m2"] or 0), r["name"] or "", r["id"]))
    for r in ranked[:top]:
        lines.append(f"  {r['score']:3d} {r['tier']:<9} {r['state'] or '--':<3} "
                     f"{(r['name'] or '(unnamed)')[:40]:<40} {r['id']}")
    lines.append("five largest grids by cell_count:")
    for r in sorted((r for r in records if r["cell_count"]), key=lambda r: -r["cell_count"])[:5]:
        lines.append(f"  {r['cell_count']:9d} cells  {r['state'] or '--':<3} "
                     f"{(r['name'] or '(unnamed)')[:40]:<40} {r['id']}")
    return "\n".join(lines)
