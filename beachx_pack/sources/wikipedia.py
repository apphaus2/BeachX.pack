"""Wikipedia first paragraph for primary beaches with a wikipedia tag. CC BY-SA 4.0."""

from __future__ import annotations

import urllib.parse
from pathlib import Path

from ..http import request_json
from .common import load_points, per_point, targets

NAME = "wikipedia"
BLOCK = "wiki"
FIELDS = ("description", "description_license", "description_source_url")
SOURCE = {"name": "Wikipedia (MediaWiki API, extracts)", "url": "https://www.wikipedia.org",
          "license": "CC BY-SA 4.0", "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
          "attribution": "Wikipedia contributors (text under CC BY-SA 4.0; each description "
                         "links its article in description_source_url)"}
SLEEP = 0.5
DEFAULT_LIMIT = 1000


def split_tag(value: str) -> tuple[str, str]:
    lang, _, title = value.partition(":")
    return lang.strip().lower(), title.strip()


def fetch(rec: dict) -> tuple[str, dict]:
    lang, title = split_tag(rec["wikipedia"])
    return request_json([f"https://{lang}.wikipedia.org/w/api.php"], params={
        "action": "query", "prop": "extracts", "exintro": "1", "explaintext": "1",
        "redirects": "1", "titles": title, "format": "json", "formatversion": "2"},
        timeout=60, attempts=3, backoff=5)


def pull(raw_dir: Path, records: list[dict], opts: dict, log=print) -> dict:
    chosen = [r for r in targets(records, ("primary",)) if r.get("wikipedia_url")]
    limit = opts.get("wikipedia_limit", DEFAULT_LIMIT)
    if opts.get("limit") is not None:
        limit = min(limit, opts["limit"])
    return per_point(NAME, raw_dir, chosen, fetch, limit=limit,
                     force=opts.get("force", False), sleep=SLEEP, log=log)


def fields(response: dict, lang: str) -> dict:
    pages = (response.get("query") or {}).get("pages") or []
    page = pages[0] if pages else {}
    paragraphs = [p.strip() for p in (page.get("extract") or "").split("\n") if p.strip()]
    if not paragraphs or page.get("missing"):
        return dict.fromkeys(FIELDS)
    title = urllib.parse.quote(page["title"].replace(" ", "_"), safe="()_,'!:*-.~/")
    return {"description": paragraphs[0], "description_license": "CC BY-SA 4.0",
            "description_source_url": f"https://{lang}.wikipedia.org/wiki/{title}"}


def fresh(raw_dir: Path, records: list[dict]) -> dict[str, dict] | None:
    points = load_points(raw_dir, NAME)
    if not points:
        return None
    out = {}
    for rid, p in points.items():
        lang = urllib.parse.urlparse(p["url"]).netloc.split(".")[0]
        out[rid] = fields(p["response"], lang)
    return out
