"""State boundaries from the Census TIGER/Line state shapefile. Public domain.

One national file, downloaded once into raw/downloads/tiger/. TIGER/Line
state polygons include each state's coastal and territorial waters (out to
three nautical miles), so a beach on the shore lies inside its state. Read
with a minimal shapefile and dBASE reader (polygons only).
"""

from __future__ import annotations

import io
import math
import struct
import zipfile
from dataclasses import dataclass

from .geometry import M_PER_DEG_LAT, M_PER_DEG_LNG
from .sources.common import download, downloads_dir

FILE = "tl_2024_us_state.zip"
URL = f"https://www2.census.gov/geo/tiger/TIGER2024/STATE/{FILE}"
SOURCE = {"name": "US Census TIGER/Line state boundaries (2024)", "url": URL,
          "license": "Public domain (US government work)", "attribution": "US Census Bureau"}
NEAR_M = 2000.0  # a point outside every state within this of one belongs to it
BAND = 0.05  # degrees of latitude per edge bucket


def read_dbf(data: bytes) -> list[dict]:
    count, head_len, rec_len = struct.unpack("<IHH", data[4:12])
    fields, pos = [], 32
    while data[pos] != 0x0D:
        name = data[pos:pos + 11].split(b"\0")[0].decode("ascii")
        fields.append((name, data[pos + 16]))
        pos += 32
    out = []
    for i in range(count):
        rec = data[head_len + i * rec_len:head_len + (i + 1) * rec_len]
        row, off = {}, 1
        for name, size in fields:
            row[name] = rec[off:off + size].decode("latin-1").strip()
            off += size
        out.append(row)
    return out


def read_shp(data: bytes, wanted: set[int] | None = None) -> dict[int, list[list[tuple[float, float]]]]:
    """Record index -> rings of (lat, lng), polygons (type 5) only."""
    out, pos, index = {}, 100, 0
    while pos + 8 <= len(data):
        _, words = struct.unpack(">ii", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + words * 2]
        pos += 8 + words * 2
        if (wanted is None or index in wanted) and struct.unpack("<i", body[:4])[0] == 5:
            nparts, npoints = struct.unpack("<ii", body[36:44])
            parts = list(struct.unpack(f"<{nparts}i", body[44:44 + 4 * nparts]))
            base = 44 + 4 * nparts
            xy = struct.unpack(f"<{2 * npoints}d", body[base:base + 16 * npoints])
            rings = []
            for k, start in enumerate(parts):
                end = parts[k + 1] if k + 1 < nparts else npoints
                rings.append([(xy[2 * j + 1], xy[2 * j]) for j in range(start, end)])
            out[index] = rings
        index += 1
    return out


@dataclass
class State:
    code: str
    name: str
    fips: str
    rings: list[list[tuple[float, float]]]

    def __post_init__(self):
        lats = [p[0] for r in self.rings for p in r]
        lngs = [p[1] for r in self.rings for p in r]
        self.bbox = (min(lats), min(lngs), max(lats), max(lngs))
        self.bands: dict[int, list[tuple]] = {}
        for ring in self.rings:
            for a, b in zip(ring, ring[1:]):
                for k in range(math.floor(min(a[0], b[0]) / BAND), math.floor(max(a[0], b[0]) / BAND) + 1):
                    self.bands.setdefault(k, []).append((a, b))

    def contains(self, lat: float, lng: float) -> bool:
        s, w, n, e = self.bbox
        if not (s <= lat <= n and w <= lng <= e):
            return False
        inside = False
        for (y1, x1), (y2, x2) in self.bands.get(math.floor(lat / BAND), ()):
            if (y1 > lat) != (y2 > lat) and lng < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
        return inside

    def distance_m(self, lat: float, lng: float, limit: float = NEAR_M) -> float:
        """Metres to the boundary, searched within `limit`; inf beyond it."""
        kx = M_PER_DEG_LNG * math.cos(math.radians(lat))
        reach = limit / M_PER_DEG_LAT
        best = math.inf
        seen = set()
        for k in range(math.floor((lat - reach) / BAND), math.floor((lat + reach) / BAND) + 1):
            for seg in self.bands.get(k, ()):
                if id(seg) in seen:
                    continue
                seen.add(id(seg))
                (y1, x1), (y2, x2) = seg
                ax, ay = (x1 - lng) * kx, (y1 - lat) * M_PER_DEG_LAT
                bx, by = (x2 - lng) * kx, (y2 - lat) * M_PER_DEG_LAT
                dx, dy = bx - ax, by - ay
                t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
                best = min(best, math.hypot(ax + t * dx, ay + t * dy))
        return best if best <= limit else math.inf


def load(codes: set[str] | None = None, log=print) -> dict[str, State]:
    """USPS code -> State for the requested codes (every state when None)."""
    path = download(URL, downloads_dir() / "tiger" / FILE, log=log)
    with zipfile.ZipFile(path) as z:
        name = lambda ext: next(n for n in z.namelist() if n.endswith(ext))  # noqa: E731
        rows = read_dbf(z.read(name(".dbf")))
        wanted = {i for i, r in enumerate(rows) if codes is None or r["STUSPS"] in codes}
        shapes = read_shp(z.read(name(".shp")), wanted)
    return {rows[i]["STUSPS"]: State(rows[i]["STUSPS"], rows[i]["NAME"], rows[i]["STATEFP"],
                                     shapes[i]) for i in sorted(wanted) if i in shapes}


def assign(lat: float, lng: float, target: State, others: dict[str, State]) -> str | None:
    """'tiger' when the point is in the target state, 'tiger_near' when it is
    in no state but within NEAR_M of the target, else None."""
    if target.contains(lat, lng):
        return "tiger"
    if any(s.contains(lat, lng) for c, s in others.items() if c != target.code):
        return None
    return "tiger_near" if target.distance_m(lat, lng) <= NEAR_M else None
