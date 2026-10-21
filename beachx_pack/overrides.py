"""Review corrections, one file per state: overrides/<st>.json, keyed by beach id."""

from __future__ import annotations

import json
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}
