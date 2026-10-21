"""Compare two packs block by block."""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

from .blocks import NAMES

MOVE_THRESHOLD_M = 250.0
SAMPLE = 10


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _git(repo: Path, *args: str) -> bytes | None:
    try:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def from_git_head(repo: Path, rel: str = "pack", state: str | None = None,
                  ) -> tuple[list[dict], dict] | None:
    """(beaches, nearby lists) of the state pack at git HEAD, or None when HEAD
    has no pack. `rel` is the state folder (pack/us/hi); a HEAD from before
    the per-state layout is read from pack/ and filtered to `state`."""
    out = _git(repo, "show", f"HEAD:{rel}/beaches.json")
    if out is not None:
        blob = _git(repo, "show", f"HEAD:{rel}/nearby.json")
        return json.loads(out), json.loads(blob) if blob else {}
    out = _git(repo, "show", "HEAD:pack/beaches.json")
    if out is None:
        return None
    nearby: dict = {}
    names = _git(repo, "ls-tree", "--name-only", "HEAD", "pack/nearby/") or b""
    for name in names.decode().split():
        blob = _git(repo, "show", f"HEAD:{name}")
        if blob:
            nearby.update(json.loads(blob))
    beaches = json.loads(out)
    if state:
        beaches = [b for b in beaches if (b.get("state") or "").upper() == state.upper()]
    return beaches, nearby


def from_path(path: Path) -> tuple[list[dict], dict]:
    """A beaches.json and the nearby lists beside it (nearby.json, or the
    older nearby/ folder)."""
    nearby: dict = {}
    single = path.parent / "nearby.json"
    if single.exists():
        nearby = json.loads(single.read_text())
    for p in sorted((path.parent / "nearby").glob("*.json")):
        nearby.update(json.loads(p.read_text()))
    return json.loads(path.read_text()), nearby


def _bhash(rec: dict, block: str) -> str | None:
    if block == "geometry" and not rec.get("blocks"):
        return rec.get("source_hash")  # packs from before blocks existed
    return ((rec.get("blocks") or {}).get(block) or {}).get("hash")


def compare(old: list[dict], new: list[dict], old_nearby: dict | None = None,
            new_nearby: dict | None = None) -> dict:
    before = {r["id"]: r for r in old}
    after = {r["id"]: r for r in new}
    shared = sorted(after.keys() & before.keys())
    out: dict = {
        "added": [after[i] for i in sorted(after.keys() - before.keys())],
        "removed": [before[i] for i in sorted(before.keys() - after.keys())],
        "renamed": [], "moved": [], "retiered": [], "nearby_items": [], "blocks": {},
    }
    for block in NAMES:
        counts = {"unchanged": 0, "changed": [], "added": 0, "removed": 0}
        for rid in shared:
            h0, h1 = _bhash(before[rid], block), _bhash(after[rid], block)
            if h0 and h1:
                if h0 == h1:
                    counts["unchanged"] += 1
                else:
                    counts["changed"].append((before[rid], after[rid]))
            elif h1:
                counts["added"] += 1
            elif h0:
                counts["removed"] += 1
        out["blocks"][block] = counts
    for a, b in out["blocks"]["geometry"]["changed"]:
        if a.get("name") != b.get("name"):
            out["renamed"].append((a, b))
        dist = haversine_m(a["latitude"], a["longitude"], b["latitude"], b["longitude"])
        if dist > MOVE_THRESHOLD_M:
            out["moved"].append((a, b, dist))
    for rid in shared:  # tier also moves with wiki and land blocks
        if before[rid].get("tier") != after[rid].get("tier"):
            out["retiered"].append((before[rid], after[rid]))
    if old_nearby is not None and new_nearby is not None:
        for a, b in out["blocks"]["nearby"]["changed"]:
            was = {(i["osm_type"], i["osm_id"]): i for i in old_nearby.get(a["id"], [])}
            now = {(i["osm_type"], i["osm_id"]): i for i in new_nearby.get(b["id"], [])}
            for key in sorted(now.keys() - was.keys()):
                out["nearby_items"].append(("added", now[key], b))
            for key in sorted(was.keys() - now.keys()):
                out["nearby_items"].append(("removed", was[key], b))
    return out


def _version(a: dict, b: dict) -> str:
    return (f"v{a.get('osm_version')} {a.get('osm_timestamp')} -> "
            f"v{b.get('osm_version')} {b.get('osm_timestamp')} "
            f"(changeset {b.get('osm_changeset')}, {b.get('osm_url')})")


def render(result: dict) -> str:
    def label(r: dict) -> str:
        return f"{r['id']} {r.get('state') or '--'} {r.get('name') or '(unnamed)'}"

    lines = [f"beaches added: {len(result['added'])}"]
    lines += [f"  {label(r)} [{r.get('tier')}]" for r in result["added"][:SAMPLE]]
    lines.append(f"beaches removed: {len(result['removed'])}")
    lines += [f"  {label(r)} [{r.get('tier')}]" for r in result["removed"][:SAMPLE]]
    lines.append("blocks (beaches in both packs):")
    for block, c in result["blocks"].items():
        lines.append(f"  {block:<10} unchanged {c['unchanged']:>6}  changed {len(c['changed']):>6}  "
                     f"added {c['added']:>6}  removed {c['removed']:>6}")
    for a, b in result["blocks"]["geometry"]["changed"][:SAMPLE]:
        lines.append(f"    geometry changed {label(b)}: {_version(a, b)}")
    for key in ("renamed", "moved", "retiered"):
        items = result[key]
        lines.append(f"{key}: {len(items)}")
        for item in items[:SAMPLE]:
            if key == "renamed":
                lines.append(f"  {item[0]['id']}: {item[0].get('name')!r} -> {item[1].get('name')!r}"
                             f"  {_version(item[0], item[1])}")
            elif key == "moved":
                lines.append(f"  {label(item[1])} moved {item[2]:.0f} m  {_version(item[0], item[1])}")
            else:
                lines.append(f"  {label(item[1])}: {item[0].get('tier')} -> {item[1].get('tier')}")
        if len(items) > SAMPLE:
            lines.append(f"  ... and {len(items) - SAMPLE} more")
    items = result["nearby_items"]
    lines.append(f"nearby items: {len(items)}")
    for what, item, beach in items[:SAMPLE * 2]:
        lines.append(f"  {item['kind']} {what} at {beach.get('name') or beach['id']}: "
                     f"{item.get('name') or '(unnamed)'} {item['osm_url']}")
    for block in NAMES[2:]:
        for a, b in result["blocks"][block]["changed"][:3]:
            diffs = [k for k in b if k not in ("blocks", "tags") and a.get(k) != b.get(k)]
            lines.append(f"  {block} changed at {label(b)}: {', '.join(diffs[:8])}")
    return "\n".join(lines)
