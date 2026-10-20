"""Deterministic score and tier. Every number lives in the SCORE table below."""

from __future__ import annotations

SCORE = {
    # OSM element
    "name": 3,                    # has a name
    "wiki_tag": 3,                # has a wikipedia or wikidata tag
    "lifeguard_tag": 1,           # lifeguard == "yes"
    "resort": 1,                  # leisure=beach_resort
    # size (area_m2)
    "area_low_m2": 50_000, "area_low": 2,
    "area_high_m2": 200_000, "area_high": 1,       # on top of area_low
    # Wikidata
    "sitelinks_low_n": 5, "sitelinks_low": 2,
    "sitelinks_high_n": 20, "sitelinks_high": 2,   # on top of sitelinks_low
    # protection: a PAD-US unit with Open access; protected_area_hint when PAD-US has no unit
    "protected": 1,
    "padus_designation": 1,       # PAD-US designation names one of DESIGNATION_WORDS
    # nearby block (within 300 m), capped as a group
    "near_lifeguard": 2, "near_parking": 1, "near_toilets": 1, "near_shower": 1,
    "near_webcam": 1,
    "nearby_cap": 4,
    # tiers
    "primary_min": 8,             # score >= 8
    "secondary_min": 4,           # 4..7; below 4 is minor; unnamed is always minor
}
DESIGNATION_WORDS = ("preserve", "park", "seashore", "recreation")
NEARBY_KINDS = {"near_lifeguard": "lifeguard", "near_parking": "parking",
                "near_toilets": "toilets", "near_shower": "shower", "near_webcam": "webcam"}
TIERS = ("primary", "secondary", "minor")


def protected(rec: dict) -> bool:
    """PAD-US, when it found a unit, replaces the name-sniffed hint: the point
    is earned only when PAD-US says public access is Open."""
    if rec.get("padus_unit_name"):
        return rec.get("public_access") == "Open"
    return bool(rec.get("protected_area_hint"))


def nearby_points(rec: dict) -> int:
    counts = rec.get("nearby_counts") or {}
    raw = sum(SCORE[key] for key, kind in NEARBY_KINDS.items() if counts.get(kind))
    return min(raw, SCORE["nearby_cap"])


def score(rec: dict) -> int:
    s = SCORE
    total = 0
    if rec.get("name"):
        total += s["name"]
    if rec.get("wikipedia") or rec.get("wikidata"):
        total += s["wiki_tag"]
    links = rec.get("wikidata_sitelinks") or 0
    if links >= s["sitelinks_low_n"]:
        total += s["sitelinks_low"]
    if links >= s["sitelinks_high_n"]:
        total += s["sitelinks_high"]
    area = rec.get("area_m2") or 0
    if area >= s["area_low_m2"]:
        total += s["area_low"]
    if area >= s["area_high_m2"]:
        total += s["area_high"]
    if protected(rec):
        total += s["protected"]
    designation = (rec.get("designation") or "").lower()
    if rec.get("padus_unit_name") and any(w in designation for w in DESIGNATION_WORDS):
        total += s["padus_designation"]
    if rec.get("lifeguard") == "yes":
        total += s["lifeguard_tag"]
    if rec.get("is_resort"):
        total += s["resort"]
    return total + nearby_points(rec)


def tier(rec: dict, value: int) -> str:
    if not rec.get("name"):
        return "minor"
    if value >= SCORE["primary_min"]:
        return "primary"
    if value >= SCORE["secondary_min"]:
        return "secondary"
    return "minor"
