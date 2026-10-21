"""A minimal GeoTIFF reader for single-band float32 rasters. Stdlib only.

Reads what the 3DEP ImageServer `exportImage` returns: little- or big-endian
TIFF, strips or tiles, no compression, deflate (8, 32946), or LZW (5), with
or without the horizontal floating-point predictor (3) or the integer
predictor (2). The georeference comes from ModelPixelScale and
ModelTiepoint (a north-up raster); pixel (col, row) has its top-left corner
at (x0 + col * sx, y0 - row * sy).
"""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass

TYPES = {1: ("B", 1), 2: ("s", 1), 3: ("H", 2), 4: ("I", 4), 5: ("II", 8), 6: ("b", 1),
         7: ("B", 1), 8: ("h", 2), 9: ("i", 4), 10: ("ii", 8), 11: ("f", 4), 12: ("d", 8),
         16: ("Q", 8)}
TAG = {"width": 256, "height": 257, "bits": 258, "compression": 259, "strip_offsets": 273,
       "samples": 277, "rows_per_strip": 278, "strip_counts": 279, "planar": 284,
       "predictor": 317, "tile_width": 322, "tile_length": 323, "tile_offsets": 324,
       "tile_counts": 325, "sample_format": 339, "pixel_scale": 33550, "tiepoint": 33922,
       "nodata": 42113}


class TiffError(ValueError):
    pass


@dataclass
class Raster:
    width: int
    height: int
    x0: float  # left edge (longitude for EPSG:4326)
    y0: float  # top edge (latitude)
    sx: float  # pixel width
    sy: float  # pixel height (positive)
    values: list[float]  # row-major, NaN where there is no data

    def at(self, col: int, row: int) -> float:
        if 0 <= col < self.width and 0 <= row < self.height:
            return self.values[row * self.width + col]
        return math.nan

    def sample(self, x: float, y: float) -> float:
        """Bilinear value at map coordinates; NaN when any of the four
        neighbouring pixels is missing. Pixel centres sit at half steps."""
        fc = (x - self.x0) / self.sx - 0.5
        fr = (self.y0 - y) / self.sy - 0.5
        c0, r0 = math.floor(fc), math.floor(fr)
        tc, tr = fc - c0, fr - r0
        v00, v10 = self.at(c0, r0), self.at(c0 + 1, r0)
        v01, v11 = self.at(c0, r0 + 1), self.at(c0 + 1, r0 + 1)
        top = v00 * (1 - tc) + v10 * tc
        bottom = v01 * (1 - tc) + v11 * tc
        return top * (1 - tr) + bottom * tr


def _lzw_decode(data: bytes) -> bytes:
    """TIFF LZW (MSB-first codes, early change)."""
    out = bytearray()
    table = [bytes([i]) for i in range(256)] + [b"", b""]
    bits, pos, nbits = 0, 0, 9
    buf = 0
    prev = None
    total = len(data) * 8
    while pos + nbits <= total:
        while bits < nbits:
            buf = (buf << 8) | data[pos // 8]
            pos += 8
            bits += 8
        bits -= nbits
        code = (buf >> bits) & ((1 << nbits) - 1)
        buf &= (1 << bits) - 1
        if code == 256:
            table = table[:258]
            nbits = 9
            prev = None
            continue
        if code == 257:
            break
        if prev is None:
            entry = table[code]
            out += entry
            prev = entry
            continue
        if code < len(table):
            entry = table[code]
            table.append(prev + entry[:1])
        else:
            entry = prev + prev[:1]
            table.append(entry)
        out += entry
        prev = entry
        if len(table) + 1 >= (1 << nbits) and nbits < 12:
            nbits += 1
    return bytes(out)


def _undo_float_predictor(chunk: bytes, width: int, rows: int) -> bytes:
    """Predictor 3: bytes are split into planes per row and differenced."""
    out = bytearray(len(chunk))
    row_bytes = width * 4
    for r in range(rows):
        row = bytearray(chunk[r * row_bytes:(r + 1) * row_bytes])
        for i in range(1, len(row)):
            row[i] = (row[i] + row[i - 1]) & 0xFF
        for c in range(width):
            for b in range(4):  # big-endian byte planes
                out[r * row_bytes + c * 4 + (3 - b)] = row[b * width + c]
    return bytes(out)


def read(data: bytes) -> Raster:
    order = {b"II": "<", b"MM": ">"}.get(data[:2])
    if order is None or struct.unpack(order + "H", data[2:4])[0] != 42:
        raise TiffError("not a classic TIFF")
    (ifd,) = struct.unpack(order + "I", data[4:8])
    (count,) = struct.unpack(order + "H", data[ifd:ifd + 2])
    tags: dict[int, tuple] = {}
    for i in range(count):
        base = ifd + 2 + 12 * i
        tag, typ, n = struct.unpack(order + "HHI", data[base:base + 8])
        fmt, size = TYPES.get(typ, ("B", 1))
        total = size * n
        raw = data[base + 8:base + 12] if total <= 4 else \
            data[struct.unpack(order + "I", data[base + 8:base + 12])[0]:][:total]
        if typ == 2:
            tags[tag] = (raw.rstrip(b"\0").decode("ascii", "replace"),)
        else:
            per = len(fmt)
            tags[tag] = struct.unpack(order + fmt * n if per == 1 else order + fmt * n, raw[:total])
    get = lambda name, default=None: tags.get(TAG[name], (default,))  # noqa: E731
    width, height = get("width")[0], get("height")[0]
    if get("bits", 32)[0] != 32 or get("sample_format", 3)[0] != 3 or get("samples", 1)[0] != 1:
        raise TiffError("only single-band float32 is supported")
    compression, predictor = get("compression", 1)[0], get("predictor", 1)[0]
    if TAG["tile_width"] in tags:
        tw, th = get("tile_width")[0], get("tile_length")[0]
        offsets, counts = tags[TAG["tile_offsets"]], tags[TAG["tile_counts"]]
    else:
        tw, th = width, get("rows_per_strip", height)[0]
        offsets, counts = tags[TAG["strip_offsets"]], tags[TAG["strip_counts"]]
    across = math.ceil(width / tw)
    values = [math.nan] * (width * height)
    nodata = None
    if TAG["nodata"] in tags:
        try:
            nodata = float(tags[TAG["nodata"]][0])
        except ValueError:
            nodata = None
    for index, (off, n) in enumerate(zip(offsets, counts)):
        chunk = data[off:off + n]
        if compression in (8, 32946):
            chunk = zlib.decompress(chunk)
        elif compression == 5:
            chunk = _lzw_decode(chunk)
        elif compression != 1:
            raise TiffError(f"compression {compression} is not supported")
        rows_here = th if TAG["tile_width"] in tags else min(th, height - index * th)
        if predictor == 3:
            chunk = _undo_float_predictor(chunk, tw, rows_here)
            fmt = ">"
        else:
            fmt = order
        need = tw * rows_here * 4
        if len(chunk) < need:  # a short last tile or strip: the rest is no data
            chunk = chunk + struct.pack(f"{fmt}f", math.nan) * ((need - len(chunk)) // 4 + 1)
        floats = struct.unpack(f"{fmt}{tw * rows_here}f", chunk[:need])
        top, left = (index // across) * th, (index % across) * tw
        if TAG["tile_width"] not in tags:
            top, left = index * th, 0
        for r in range(rows_here):
            row = top + r
            if row >= height:
                break
            span = min(tw, width - left)
            values[row * width + left:row * width + left + span] = floats[r * tw:r * tw + span]
    lowest = -1e30
    for i, v in enumerate(values):
        if v != v or v < lowest or (nodata is not None and v == nodata) or abs(v) > 1e30:
            values[i] = math.nan
    sx, sy = get("pixel_scale")[0:2] if TAG["pixel_scale"] in tags else (1.0, 1.0)
    tie = tags.get(TAG["tiepoint"], (0, 0, 0, 0, 0, 0))
    x0 = tie[3] - tie[0] * sx
    y0 = tie[4] + tie[1] * sy
    return Raster(width, height, x0, y0, sx, sy, list(values))
