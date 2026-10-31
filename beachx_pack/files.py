"""Directory listings that skip macOS AppleDouble sidecars.

A Mac writing to an SMB share that has no native extended attributes stores
each file's metadata beside it as `._<name>`. raw/ lives on such a share
(RAW.md), so every listing of raw/ or pack/ goes through `listed`, never a
bare glob: `._beach_1.json` matches `*.json` and is not JSON.
"""

from __future__ import annotations

from pathlib import Path


def listed(folder: Path, pattern: str = "*") -> list[Path]:
    """`folder.glob(pattern)` sorted, without `._` sidecars."""
    return sorted(p for p in folder.glob(pattern) if not p.name.startswith("._"))
