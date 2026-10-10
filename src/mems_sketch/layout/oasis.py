"""OASIS files, written (SEMI P39): polygons, cut into pieces without holes,
and placements, arrays as repetitions. Uncompressed, without name tables."""

from __future__ import annotations

import itertools
import struct

from mems_sketch.layout.model import Layout, Placement, hole_free

MAGIC = b"%SEMI-OASIS\r\n"
START, END, CELL_BY_NAME, PLACEMENT, PLACEMENT_TRANSFORMED, POLYGON = 1, 2, 14, 17, 18, 21


def _uint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _sint(value: int) -> bytes:
    return _uint((abs(value) << 1) | (1 if value < 0 else 0))


def _string(text: str) -> bytes:
    data = text.encode("ascii", "replace")
    return _uint(len(data)) + data


def _real(value: float) -> bytes:
    if value == int(value) and value >= 0:
        return _uint(0) + _uint(int(value))  # a positive whole number
    if value == int(value):
        return _uint(1) + _uint(int(-value))
    return _uint(7) + struct.pack("<d", value)  # IEEE double, little-endian


def _gdelta(dx: int, dy: int) -> bytes:
    """A g-delta in its second form: any direction."""
    return _uint((abs(dx) << 2) | ((1 if dx < 0 else 0) << 1) | 1) + _sint(dy)


def write(layout: Layout) -> bytes:
    out = bytearray(MAGIC)
    # START: version, unit (grid steps per µm), table offsets here (flag 0), all zero.
    out += _uint(START) + _string("1.0") + _real(round(1 / layout.dbu, 9)) + _uint(0)
    out += _uint(0) * 12
    for cell in layout.cells.values():
        out += _uint(CELL_BY_NAME) + _string(cell.name)
        for (layer, datatype), polygons in cell.polygons.items():
            for polygon in polygons:
                for piece in hole_free(polygon):
                    out += _polygon(layer, datatype, piece.hull)
        for placement in cell.placements:
            out += _placement(placement)
    # END: padding to 256 bytes, no validation.
    end = _uint(END)
    tail = _uint(0)  # validation scheme: none
    padding = 256 - len(end) - len(tail)
    body = b"\0" * (padding - len(_uint(padding)))
    # The padding is a b-string whose length is part of it: fix it up.
    for _ in range(3):
        pad = _uint(len(body)) + body
        extra = len(end) + len(pad) + len(tail) - 256
        if extra == 0:
            break
        body = body[: len(body) - extra] if extra > 0 else body + b"\0" * (-extra)
    out += end + _uint(len(body)) + body + tail
    return bytes(out)


def _polygon(layer: int, datatype: int, points) -> bytes:
    ring = [tuple(p) for p in points.tolist()]
    x0, y0 = ring[0]
    deltas = b"".join(_gdelta(b[0] - a[0], b[1] - a[1]) for a, b in itertools.pairwise(ring))
    point_list = _uint(4) + _uint(len(ring) - 1) + deltas  # all-angle; the first point is (x, y)
    info = 0b0011_1011  # point list, x, y, datatype, layer
    return (
        _uint(POLYGON)
        + bytes([info])
        + _uint(layer)
        + _uint(datatype)
        + point_list
        + _sint(x0)
        + _sint(y0)
    )


def _placement(p: Placement) -> bytes:
    repetition = b""
    if p.columns > 1 and p.rows > 1:
        repetition = (
            _uint(8)
            + _uint(p.columns - 2)
            + _uint(p.rows - 2)
            + _gdelta(*p.column_step)
            + _gdelta(*p.row_step)
        )
    elif p.columns > 1 or p.rows > 1:
        count, step = (p.columns, p.column_step) if p.columns > 1 else (p.rows, p.row_step)
        repetition = _uint(9) + _uint(count - 2) + _gdelta(*step)
    turns = p.quarter_turns
    flags = (
        (1 << 7) | (1 << 5) | (1 << 4) | ((1 << 3) if repetition else 0) | (1 if p.mirror else 0)
    )
    if turns is not None:  # C N=name X Y R AA F
        record = _uint(PLACEMENT) + bytes([flags | (turns << 1)]) + _string(p.cell)
    else:  # C N X Y R M A F
        flags |= (1 << 2) | (1 << 1)
        record = (
            _uint(PLACEMENT_TRANSFORMED)
            + bytes([flags])
            + _string(p.cell)
            + _real(p.mag)
            + _real(p.angle)
        )
    return record + _sint(p.x) + _sint(p.y) + repetition
