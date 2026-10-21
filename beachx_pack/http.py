"""Small polite HTTP helper: User-Agent, retry with backoff, endpoint fallback."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import USER_AGENT

RETRY_CODES = {429, 500, 502, 503, 504}
# An Overpass server whose database is older than this is serving stale data
# (overpass.kumi.systems answered from May to July 2026 data in October 2026
# and missed every beach mapped since). Its answer is refused and the next
# endpoint is asked; a stale server is not retried.
MAX_OSM_AGE_DAYS = 3.0


class FetchError(RuntimeError):
    pass


class StaleServer(FetchError):
    """The endpoint answered from an old database: move on to the next one."""


def check_osm_age(parsed, now: datetime | None = None) -> None:
    """Raise StaleServer when an Overpass answer's `osm3s.timestamp_osm_base`
    is more than MAX_OSM_AGE_DAYS old. Answers without one pass."""
    base = (parsed.get("osm3s") or {}).get("timestamp_osm_base") if isinstance(parsed, dict) else None
    if not base:
        return
    try:
        when = datetime.fromisoformat(base.replace("Z", "+00:00"))
    except ValueError:
        return
    age = ((now or datetime.now(timezone.utc)) - when).total_seconds() / 86400
    if age > MAX_OSM_AGE_DAYS:
        raise StaleServer(f"server data is {age:.0f} days old (timestamp_osm_base {base})")


def request_json(urls: list[str], *, data: dict | None = None, params: dict | None = None,
                 timeout: float = 400, attempts: int = 4, backoff: float = 20,
                 accept_status: frozenset[int] = frozenset(), raw_text: bool = False,
                 headers: dict | None = None, validate=None, log=print) -> tuple[str, dict]:
    """GET (params) or POST (data) each URL in turn until one returns JSON.

    Each URL gets `attempts` tries with doubling sleeps on 429/5xx, network
    errors, and Overpass runtime-error remarks. Returns (url, parsed json).
    A status in `accept_status` (for example 404 for "no data here") returns
    {"_http_status": code} instead of raising. `raw_text` returns {"text": body}.
    `validate(parsed)` may raise FetchError to reject a well-formed but wrong
    answer (it is then retried like a 5xx).
    """
    last = "no endpoints"
    for url in urls:
        wait = backoff
        for attempt in range(1, attempts + 1):
            full = url + ("?" + urllib.parse.urlencode(params) if params else "")
            body = urllib.parse.urlencode(data).encode() if data is not None else None
            req = urllib.request.Request(full, data=body, headers={
                "User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    content = resp.read()
                if raw_text:
                    return full, {"text": content.decode("utf-8", "replace")}
                parsed = json.loads(content)
                remark = parsed.get("remark") if isinstance(parsed, dict) else None
                if remark and "error" in remark.lower():
                    raise FetchError(f"server remark: {remark.strip()[:200]}")
                if isinstance(parsed, dict) and parsed.get("error", {}).get("code") == "maxlag":
                    raise FetchError("wikidata maxlag")
                check_osm_age(parsed)
                if validate:
                    validate(parsed)
                return full, parsed
            except urllib.error.HTTPError as err:
                if err.code in accept_status:
                    return full, {"_http_status": err.code}
                last = f"HTTP {err.code} from {url}"
                if err.code not in RETRY_CODES:
                    raise FetchError(last) from err
                retry_after = err.headers.get("Retry-After") if err.headers else None
                if retry_after and retry_after.isdigit():
                    wait = max(wait, float(retry_after))
            except StaleServer as err:
                last = f"{type(err).__name__}: {err} ({url})"
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError, FetchError,
                    json.JSONDecodeError) as err:
                last = f"{type(err).__name__}: {err} ({url})"
            if attempt < attempts:
                log(f"    retry {attempt}/{attempts - 1} in {wait:.0f}s after {last}")
                time.sleep(wait)
                wait *= 2
        log(f"    giving up on {url}: {last}")
    raise FetchError(last)
