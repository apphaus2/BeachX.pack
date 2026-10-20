"""EPA BEACON beach ids: not pulled yet.

No stable, documented download of BEACON monitored beaches with coordinates
was found (see README, "Not pulled yet"). This source records that in the
manifest and leaves beacon_beach_id null.
"""

from __future__ import annotations

from pathlib import Path

NAME = "beacon"
BLOCK = "health"
FIELDS = ("beacon_beach_id", "beacon_match_confidence")
SOURCE = {"name": "EPA BEACON (not pulled yet)", "url": "https://beacon.epa.gov/ords/beacon2/",
          "license": "Public domain (US government work)", "attribution": "US EPA BEACON"}
NOTE = ("not pulled: no stable BEACON beach list with coordinates was found; "
        "see README 'Not pulled yet'")


def pull(raw_dir: Path, records: list[dict], opts: dict, log=print) -> dict:
    log(f"  {NAME}: {NOTE}")
    return {"calls": 0, "ok": 0, "errors": 0, "cached": 0, "endpoint": None, "note": NOTE,
            "skipped": True}


def fresh(raw_dir: Path, records: list[dict]) -> dict[str, dict] | None:
    return None
