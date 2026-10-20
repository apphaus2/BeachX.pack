"""Small polite HTTP helper: User-Agent, retry with backoff, endpoint fallback."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import USER_AGENT

RETRY_CODES = {429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    pass


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
            except (urllib.error.URLError, TimeoutError, ConnectionError, FetchError,
                    json.JSONDecodeError) as err:
                last = f"{type(err).__name__}: {err} ({url})"
            if attempt < attempts:
                log(f"    retry {attempt}/{attempts - 1} in {wait:.0f}s after {last}")
                time.sleep(wait)
                wait *= 2
        log(f"    giving up on {url}: {last}")
    raise FetchError(last)
