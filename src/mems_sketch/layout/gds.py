"""GDSII stream files, read and written.

Written: boundaries (polygons, cut into pieces without holes), and cell
references, arrays as AREF. Read: boundaries, boxes and paths (as
polygons), and cell references with their reflection, angle and
magnification; texts and nodes are skipped.
"""

from __future__ import annotations

import math
import os
import struct
import time

import numpy as np

from mems_sketch.core.region import IntPolygon
from mems_sketch.layout.model import Cell, Layout, Placement, hole_free

FIXED_DATE = 946684800  # 2000-01-01 00:00:00 UTC: when nothing else is said

# Record types (high byte) and their data types (low byte).
HEADER, BGNLIB, LIBNAME, UNITS, ENDLIB = 0x0002, 0x0102, 0x0206, 0x0305, 0x0400
BGNSTR, STRNAME, ENDSTR = 0x0502, 0x0606, 0x0700
BOUNDARY, PATH, SREF, AREF, TEXT, LAYER, DATATYPE = (
    0x0800,
    0x0900,
    0x0A00,
    0x0B00,
    0x0C00,
    0x0D02,
    0x0E02,
)
WIDTH, XY, ENDEL, SNAME, COLROW = 0x0F03, 0x1003, 0x1100, 0x1206, 0x1302
NODE, STRANS, MAG, ANGLE = 0x1500, 0x1A01, 0x1B05, 0x1C05
PATHTYPE, BOX, BGNEXTN, ENDEXTN = 0x2102, 0x2D00, 0x3003, 0x3103


# -- the 8-byte real (excess-64, base 16) -----------------------------------------------


def _real8(value: float) -> bytes:
    if value == 0:
        return b"\0" * 8
    sign = 0x80 if value < 0 else 0
    value = abs(value)
    exponent = 64
    while value >= 1:
        value /= 16
        exponent += 1
    while value < 1 / 16:
        value *= 16
        exponent -= 1
    mantissa = round(value * (1 << 56))
    if mantissa >= 1 << 56:  # rounding carried into a new digit
        mantissa >>= 4
        exponent += 1
    return bytes([sign | exponent]) + mantissa.to_bytes(7, "big")


def _from_real8(data: bytes) -> float:
    sign = -1 if data[0] & 0x80 else 1
    exponent = (data[0] & 0x7F) - 64
    mantissa = int.from_bytes(data[1:8], "big")
    return sign * mantissa / (1 << 56) * 16.0**exponent


# -- writing --------------------------------------------------------------------------


def _record(kind: int, payload: bytes = b"") -> bytes:
    if len(payload) % 2:
        payload += b"\0"
    return struct.pack(">HH", len(payload) + 4, kind) + payload


def _string(kind: int, text: str) -> bytes:
    return _record(kind, text.encode("ascii", "replace"))


def _ints2(kind: int, *values: int) -> bytes:
    return _record(kind, struct.pack(f">{len(values)}h", *values))


def _points(points) -> bytes:
    return _record(XY, np.asarray(points, dtype=">i4").tobytes())


def _date() -> tuple[int, ...]:
    """The date written into the file: fixed, so that the same design gives the same
    bytes on every run (requirement QP-5), or SOURCE_DATE_EPOCH's, as reproducible
    builds set it."""
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    stamp = time.gmtime(int(epoch)) if epoch.isdigit() else time.gmtime(FIXED_DATE)
    return (stamp.tm_year, stamp.tm_mon, stamp.tm_mday, stamp.tm_hour, stamp.tm_min, stamp.tm_sec)


def write(layout: Layout) -> bytes:
    date = _date()
    out = [
        _ints2(HEADER, 600),
        _ints2(BGNLIB, *date, *date),
        _string(LIBNAME, "LIB"),
        _record(UNITS, _real8(layout.dbu) + _real8(layout.dbu * 1e-6)),
    ]
    for cell in layout.cells.values():
        out += [_ints2(BGNSTR, *date, *date), _string(STRNAME, cell.name)]
        for (layer, datatype), polygons in cell.polygons.items():
            for polygon in polygons:
                for piece in hole_free(polygon):
                    ring = np.vstack([piece.hull, piece.hull[:1]])
                    out += [
                        _record(BOUNDARY),
                        _ints2(LAYER, layer),
                        _ints2(DATATYPE, datatype),
                        _points(ring),
                        _record(ENDEL),
                    ]
        for placement in cell.placements:
            out += _reference(placement)
        out.append(_record(ENDSTR))
    out.append(_record(ENDLIB))
    return b"".join(out)


def _reference(p: Placement) -> list[bytes]:
    array = p.columns > 1 or p.rows > 1
    out = [_record(AREF if array else SREF), _string(SNAME, p.cell)]
    if p.mirror or p.angle or p.mag != 1:
        out.append(_record(STRANS, struct.pack(">H", 0x8000 if p.mirror else 0)))
        if p.mag != 1:
            out.append(_record(MAG, _real8(p.mag)))
        if p.angle:
            out.append(_record(ANGLE, _real8(p.angle)))
    if array:
        out.append(_ints2(COLROW, p.columns, p.rows))
        corner_c = (p.x + p.columns * p.column_step[0], p.y + p.columns * p.column_step[1])
        corner_r = (p.x + p.rows * p.row_step[0], p.y + p.rows * p.row_step[1])
        out.append(_points([(p.x, p.y), corner_c, corner_r]))
    else:
        out.append(_points([(p.x, p.y)]))
    out.append(_record(ENDEL))
    return out


# -- reading --------------------------------------------------------------------------


def _records(data: bytes):
    position = 0
    while position + 4 <= len(data):
        length, kind = struct.unpack_from(">HH", data, position)
        if length < 4:
            if length == 0:  # padding after ENDLIB
                return
            raise ValueError("not a readable GDS file: a record shorter than its header")
        yield kind, data[position + 4 : position + length]
        position += length


def _int2s(payload: bytes) -> list[int]:
    return list(struct.unpack(f">{len(payload) // 2}h", payload))


def _xy(payload: bytes) -> list[tuple[int, int]]:
    values = struct.unpack(f">{len(payload) // 4}i", payload)
    return list(zip(values[0::2], values[1::2], strict=True))


def _text(payload: bytes) -> str:
    return payload.rstrip(b"\0").decode("ascii", "replace")


def read(data: bytes) -> Layout:
    """The layout in a GDS file's content."""
    if len(data) < 4 or struct.unpack_from(">H", data, 2)[0] != HEADER:
        raise ValueError("not a readable GDS file")
    layout = Layout()
    cell: Cell | None = None
    element: dict | None = None
    for kind, payload in _records(data):
        if kind == UNITS:  # the database unit in metres, whatever the user unit
            layout.dbu = _from_real8(payload[8:16]) / 1e-6
        elif kind == BGNSTR:
            cell = None
        elif kind == STRNAME:
            cell = layout.cell(_text(payload))
        elif kind in (BOUNDARY, PATH, BOX, SREF, AREF, TEXT, NODE):
            element = {"kind": kind}
        elif element is not None and kind == ENDEL:
            _finish(cell, element)
            element = None
        elif element is not None:
            if kind in (LAYER, DATATYPE, PATHTYPE, 0x2E02):  # 0x2E02: BOXTYPE
                element[kind] = _int2s(payload)[0]
            elif kind in (WIDTH, BGNEXTN, ENDEXTN):
                element[kind] = struct.unpack(">i", payload[:4])[0]
            elif kind == XY:
                element[XY] = _xy(payload)
            elif kind in (SNAME,):
                element[SNAME] = _text(payload)
            elif kind == STRANS:
                element[STRANS] = struct.unpack(">H", payload[:2])[0]
            elif kind in (MAG, ANGLE):
                element[kind] = _from_real8(payload)
            elif kind == COLROW:
                element[COLROW] = _int2s(payload)
        elif kind == ENDLIB:
            break
    if not layout.cells:
        raise ValueError("not a readable GDS file: it has no cells")
    return layout


def _finish(cell: Cell | None, e: dict) -> None:
    if cell is None:
        return
    kind = e["kind"]
    layer = (e.get(LAYER, 0), e.get(DATATYPE, e.get(0x2E02, 0)))
    points = e.get(XY, [])
    if kind in (BOUNDARY, BOX) and len(points) >= 3:
        ring = points[:-1] if points[0] == points[-1] else points
        if len(ring) >= 3:
            cell.add(layer, IntPolygon(_counter_clockwise(ring), []))
    elif kind == PATH and len(points) >= 2:
        polygon = _path_polygon(points, e.get(WIDTH, 0), e.get(PATHTYPE, 0), e)
        if polygon is not None:
            cell.add(layer, polygon)
    elif kind in (SREF, AREF) and points:
        strans = e.get(STRANS, 0)
        placement = Placement(
            e.get(SNAME, ""),
            *points[0],
            angle=e.get(ANGLE, 0.0),
            mirror=bool(strans & 0x8000),
            mag=e.get(MAG, 1.0),
        )
        if kind == AREF and len(points) >= 3 and COLROW in e:
            columns, rows = e[COLROW]
            (x0, y0), (xc, yc), (xr, yr) = points[:3]
            placement.columns, placement.rows = max(1, columns), max(1, rows)
            placement.column_step = (
                round((xc - x0) / placement.columns),
                round((yc - y0) / placement.columns),
            )
            placement.row_step = (
                round((xr - x0) / placement.rows),
                round((yr - y0) / placement.rows),
            )
        cell.placements.append(placement)


def _counter_clockwise(ring: list[tuple[int, int]]) -> list[tuple[int, int]]:
    twice = sum(
        x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1], strict=True)
    )
    return ring if twice >= 0 else ring[::-1]


def _path_polygon(points, width: int, pathtype: int, e: dict) -> IntPolygon | None:
    """A path as a polygon: its centreline grown by half its width on either
    side, the ends flush (0), extended by half the width (1 round, taken as 2,
    and 2), or by their own extensions (4)."""
    half = abs(width) / 2
    if half == 0:
        return None
    begin = end = 0.0
    if pathtype in (1, 2):
        begin = end = half
    elif pathtype == 4:
        begin, end = e.get(BGNEXTN, 0), e.get(ENDEXTN, 0)
    pts = [points[0]] + [p for k, p in enumerate(points[1:], 1) if p != points[k - 1]]
    if len(pts) < 2:
        return None

    def unit(a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        return dx / length, dy / length

    (sx, sy), (ex, ey) = unit(pts[0], pts[1]), unit(pts[-2], pts[-1])
    pts = [
        (pts[0][0] - sx * begin, pts[0][1] - sy * begin),
        *pts[1:-1],
        (pts[-1][0] + ex * end, pts[-1][1] + ey * end),
    ]
    left, right = [], []
    for k, (x, y) in enumerate(pts):
        if k == 0:
            nx, ny = -unit(pts[0], pts[1])[1], unit(pts[0], pts[1])[0]
            scale = 1.0
        elif k == len(pts) - 1:
            nx, ny = -unit(pts[-2], pts[-1])[1], unit(pts[-2], pts[-1])[0]
            scale = 1.0
        else:  # miter: along the bisector of the two normals
            ax, ay = unit(pts[k - 1], pts[k])
            bx, by = unit(pts[k], pts[k + 1])
            n1, n2 = (-ay, ax), (-by, bx)
            nx, ny = n1[0] + n2[0], n1[1] + n2[1]
            length = math.hypot(nx, ny) or 1.0
            nx, ny = nx / length, ny / length
            scale = 1 / max(0.1, nx * n1[0] + ny * n1[1])
        left.append((round(x + nx * half * scale), round(y + ny * half * scale)))
        right.append((round(x - nx * half * scale), round(y - ny * half * scale)))
    return IntPolygon(_counter_clockwise(right + left[::-1]), [])
