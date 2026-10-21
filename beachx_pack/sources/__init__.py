"""Enrichment sources. Each module exposes:

NAME, BLOCK, FIELDS, SOURCE (manifest entry with URL and license),
pull(raw_dir, records, opts, log) -> status dict   (fetch into raw/<run-date>/<NAME>/)
fresh(raw_dir, records) -> {beach id: fields}       (only beaches this run covers)
"""

from __future__ import annotations

from .common import targets  # noqa: F401  (re-exported)
from . import buoys, census, nws, padus, tides, timezone, wikidata, wikipedia

MODULES = {m.NAME: m for m in (tides, buoys, census, timezone, padus, nws, wikidata,
                               wikipedia)}
ORDER = ("tides", "buoys", "timezone", "census", "padus", "nws", "wikidata", "wikipedia")

