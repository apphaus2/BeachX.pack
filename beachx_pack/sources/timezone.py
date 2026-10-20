"""IANA time zone from a built-in table. No download.

State -> zone, with the coastal splits that matter:
- Florida: longitude west of -85.0 is America/Chicago (the Apalachicola River
  is the real line; -85.0 approximates it. Known miss: Port St. Joe, Cape San
  Blas and the St. Joseph Peninsula in Gulf County are, as far as we know,
  Eastern but fall west of -85.0).
- Alaska: west of -169.5 and south of 55 N (the Aleutians), or east of the
  180th meridian, is America/Adak. The Pribilofs and St. Lawrence Island stay
  America/Anchorage.
- Michigan: the Upper Peninsula counties on Wisconsin time are
  America/Menominee: Menominee County shore (south of 46 N, west of -87.35)
  and Gogebic County shore (west of -89.36).
- Indiana: the Lake Michigan shore counties (west of -86.52) are
  America/Chicago; the rest is America/Indiana/Indianapolis.
"""

from __future__ import annotations

from pathlib import Path

NAME = "timezone"
BLOCK = "places"
FIELDS = ("timezone",)
SOURCE = {"name": "Built-in state to IANA time zone table", "url": None,
          "license": "n/a (rules in beachx_pack/sources/timezone.py)", "attribution": None}

EASTERN, CENTRAL, PACIFIC = "America/New_York", "America/Chicago", "America/Los_Angeles"
STATE_ZONE = {
    "ME": EASTERN, "NH": EASTERN, "MA": EASTERN, "RI": EASTERN, "CT": EASTERN, "NY": EASTERN,
    "NJ": EASTERN, "PA": EASTERN, "DE": EASTERN, "MD": EASTERN, "DC": EASTERN, "VA": EASTERN,
    "NC": EASTERN, "SC": EASTERN, "GA": EASTERN, "FL": EASTERN, "OH": EASTERN,
    "MI": "America/Detroit", "IN": "America/Indiana/Indianapolis",
    "AL": CENTRAL, "MS": CENTRAL, "LA": CENTRAL, "TX": CENTRAL, "IL": CENTRAL, "WI": CENTRAL,
    "MN": CENTRAL, "CA": PACIFIC, "OR": PACIFIC, "WA": PACIFIC,
    "AK": "America/Anchorage", "HI": "Pacific/Honolulu",
    "PR": "America/Puerto_Rico", "VI": "America/St_Thomas",
}


def zone(state: str | None, lat: float, lon: float) -> str | None:
    if state is None:
        return None
    if state == "FL" and lon < -85.0:
        return CENTRAL
    if state == "AK" and (lon > 0 or (lon < -169.5 and lat < 55.0)):
        return "America/Adak"
    if state == "MI" and ((lat < 46.0 and lon < -87.35) or lon < -89.36):
        return "America/Menominee"
    if state == "IN" and lon < -86.52:
        return CENTRAL
    return STATE_ZONE.get(state)


def pull(raw_dir: Path, records: list[dict], opts: dict, log=print) -> dict:
    log(f"  {NAME}: table only, nothing to fetch")
    return {"calls": 0, "ok": 0, "errors": 0, "cached": 0, "endpoint": None, "note": "built-in table"}


def fresh(raw_dir: Path, records: list[dict]) -> dict[str, dict] | None:
    from ..blocks import read_statuses
    if NAME not in read_statuses(raw_dir):
        return None
    return {r["id"]: {"timezone": zone(r.get("census_state") or r["state"], r["latitude"],
                                       r["longitude"])} for r in records}
