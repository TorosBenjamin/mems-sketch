"""The built-in rule kinds (requirement DRC-11). Each is a plugin like any
other (see :mod:`mems_sketch.process.rules`): a name, the layers it takes by
role, its parameters, and a check on regions (:class:`~mems_sketch.core.region.Region`)
in database units.

Widths and spacings are checked by growing and shrinking: what shrinking by
half the width and growing back removes is narrower than the width; what
growing by half the spacing and shrinking back adds is a gap narrower than
the spacing. Corners keep their shape (miter joins), so right-angled corners
pass; at a diagonal corner-to-corner gap the distance is measured along x
and y, which is stricter than the straight-line distance.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import ClassVar

from mems_sketch.core.region import Box, IntPolygon, Region, measures
from mems_sketch.options import Option
from mems_sketch.process.rules import Finding

DISTANCE = Option("value", 1.0, "Distance", minimum=0, suffix=" µm")
SLIVER_NM = 2  # pieces thinner than this are rounding, not violations


def _box(box: Box, dbu: float) -> tuple[float, float, float, float]:
    return (box.left * dbu, box.bottom * dbu, box.right * dbu, box.top * dbu)


def _outline(piece: IntPolygon) -> tuple[tuple[int, int], ...]:
    """Where a finding is, exactly: its outline in database units."""
    return tuple((int(x), int(y)) for x, y in piece.hull.tolist())


def _pieces(polygons: Iterable[IntPolygon], dbu: float, message: str) -> list[Finding]:
    return [Finding(message, _box(p.bbox(), dbu), outline=_outline(p)) for p in polygons]


MEASURED_MAX = 64  # pieces measured one by one; more are reported without their size


def _half_width(piece: IntPolygon) -> int:
    """The largest inset (database units) that leaves something of ``piece``: half
    its widest part, less one."""
    region = Region(piece)
    box = piece.bbox()
    low, high = 0, max(1, min(box.right - box.left, box.top - box.bottom) // 2)
    while low < high:  # the largest k with something left after insetting by k
        middle = (low + high + 1) // 2
        if region.sized(-middle).is_empty():
            high = middle - 1
        else:
            low = middle
    return low


def _measured(polygons: Sequence[IntPolygon], dbu: float, message, inset: int = 0) -> list[Finding]:
    """A finding per piece, its message made by ``message(width_um)``: the width of
    its widest part, plus ``2 × inset`` (what was taken off before it was found).
    Past MEASURED_MAX pieces, ``message(None)``."""
    found = []
    for index, piece in enumerate(polygons):
        width = None
        if index < MEASURED_MAX:
            width = round(2 * (inset + _half_width(piece) + 1) * dbu, 6)
        found.append(Finding(message(width), _box(piece.bbox(), dbu), width, _outline(piece)))
    return found


def _um(value: float) -> str:
    return f"{round(value, 3):g}"


def _real(region: Region) -> list[IntPolygon]:
    """The pieces of ``region`` that are more than a rounding sliver."""
    pieces = region.each_merged()
    areas, perimeters = measures(pieces)
    return [
        p
        for p, a, length in zip(pieces, areas, perimeters, strict=True)
        if a > SLIVER_NM * length / 2
    ]


def _dbu(value_um: float, dbu: float) -> int:
    return round(value_um / dbu)


def _opening(region: Region, half: int) -> Region:
    return region.sized(-half).sized(half)


def _closing(region: Region, half: int) -> Region:
    return region.sized(half).sized(-half)


def _interacting(pieces: Region, other: Region) -> tuple[list[IntPolygon], list[IntPolygon]]:
    """The pieces of ``pieces`` that touch or overlap ``other``, and the others."""
    near = other.sized(1)  # touching counts
    touching, alone = [], []
    box = near.bbox()
    for piece in pieces.each_merged():
        b = piece.bbox()
        overlaps = not box.empty() and not (
            b.right < box.left or b.left > box.right or b.top < box.bottom or b.bottom > box.top
        )
        if overlaps and not (Region(piece) & near).is_empty():
            touching.append(piece)
        else:
            alone.append(piece)
    return touching, alone


class _Kind:
    name: ClassVar[str]
    title: ClassVar[str]
    roles: ClassVar[tuple[str, ...]] = ("layer",)
    parameters: ClassVar[tuple[Option, ...]] = (DISTANCE,)


# -- one layer -----------------------------------------------------------------


class MinWidth(_Kind):
    name = "min_width"
    title = "Minimum width"

    def check(self, regions: Sequence[Region], dbu: float, value: float) -> list[Finding]:
        region = regions[0].merged()
        # Shrinking by a little under half the width removes only what is
        # narrower than it, so features exactly at the limit pass.
        narrow = region - _opening(region, max(0, (_dbu(value, dbu) - 2) // 2))
        return _measured(
            _real(narrow),
            dbu,
            lambda w: f"{_um(w)} µm wide < {value:g} µm" if w else f"width < {value:g} µm",
        )


class MinSpace(_Kind):
    name = "min_space"
    title = "Minimum spacing"

    def check(self, regions: Sequence[Region], dbu: float, value: float) -> list[Finding]:
        region = regions[0].merged()
        # Growing by a little under half the spacing closes only narrower gaps.
        gaps = _closing(region, max(0, (_dbu(value, dbu) - 1) // 2)) - region
        return _measured(
            _real(gaps),
            dbu,
            lambda w: f"{_um(w)} µm gap < {value:g} µm" if w else f"spacing < {value:g} µm",
        )


class MaxWidth(_Kind):
    name = "max_width"
    title = "Maximum width"

    def check(self, regions: Sequence[Region], dbu: float, value: float) -> list[Finding]:
        # What survives shrinking by half the width is wider than the width.
        inset = _dbu(value, dbu) // 2 + 1
        wide = regions[0].merged().sized(-inset)
        return _measured(
            wide.each_merged(),
            dbu,
            lambda w: f"{_um(w)} µm wide > {value:g} µm" if w else f"wider than {value:g} µm",
            inset,
        )


class MinArea(_Kind):
    name = "min_area"
    title = "Minimum area"
    parameters = (Option("value", 1.0, "Area", minimum=0, suffix=" µm²"),)

    def check(self, regions: Sequence[Region], dbu: float, value: float) -> list[Finding]:
        limit = math.ceil(value / dbu**2)
        small = [p for p in regions[0].each_merged() if p.area() < limit]
        return _pieces(small, dbu, f"area < {value:g} µm²")


class MinHoleArea(_Kind):
    name = "min_hole_area"
    title = "Minimum hole area"
    parameters = (Option("value", 1.0, "Area", minimum=0, suffix=" µm²"),)

    def check(self, regions: Sequence[Region], dbu: float, value: float) -> list[Finding]:
        limit = math.ceil(value / dbu**2)
        holes = [IntPolygon(h, []) for p in regions[0].each_merged() for h in p.holes]
        small = [h for h in holes if h.area() < limit]
        return _pieces(small, dbu, f"hole area < {value:g} µm²")


class Connected(_Kind):
    name = "connected"
    title = "Number of pieces"
    parameters = (Option("pieces", 1, "Pieces", minimum=1),)

    def check(self, regions: Sequence[Region], dbu: float, pieces: int) -> list[Finding]:
        merged = regions[0].merged()
        count = merged.count()
        if count in (0, pieces):
            return []
        return [Finding(f"{count} pieces, not {pieces}", _box(merged.bbox(), dbu))]


# -- two layers ----------------------------------------------------------------


class Enclosure(_Kind):
    name = "enclosure"
    title = "Enclosure"
    roles = ("outer", "inner")

    def check(self, regions: Sequence[Region], dbu: float, value: float) -> list[Finding]:
        outer, inner = regions[0].merged(), regions[1].merged()
        outside = inner - outer
        short = inner.sized(_dbu(value, dbu)) - outer
        if not outside.is_empty():  # what is not enclosed at all is reported as that
            short = short - outside.sized(_dbu(value, dbu))
        return _pieces(_real(short), dbu, f"enclosed by less than {value:g} µm") + _pieces(
            _real(outside), dbu, "not enclosed"
        )


class Separation(_Kind):
    name = "separation"
    title = "Separation"
    roles = ("first", "second")

    def check(self, regions: Sequence[Region], dbu: float, value: float) -> list[Finding]:
        first, second = regions[0].merged(), regions[1].merged()
        half = -(-_dbu(value, dbu) // 2)
        overlap = first & second
        close = (first.sized(half) & second.sized(half)) - overlap.sized(half)
        return _pieces(_real(close), dbu, f"closer than {value:g} µm") + _pieces(
            _real(overlap), dbu, "overlapping"
        )


class Inside(_Kind):
    name = "inside"
    title = "Inside"
    roles = ("inner", "outer")
    parameters = ()

    def check(self, regions: Sequence[Region], dbu: float) -> list[Finding]:
        inner, outer = regions
        return _pieces(_real(inner - outer), dbu, "outside")


class NotOverlapping(_Kind):
    name = "not_overlapping"
    title = "Not overlapping"
    roles = ("first", "second")
    parameters = ()

    def check(self, regions: Sequence[Region], dbu: float) -> list[Finding]:
        first, second = regions
        return _pieces(_real(first & second), dbu, "overlapping")


# -- MEMS topology ---------------------------------------------------------------


class Anchored(_Kind):
    name = "anchored"
    title = "Anchored"
    roles = ("layer", "anchor")
    parameters = ()

    def check(self, regions: Sequence[Region], dbu: float) -> list[Finding]:
        layer, anchor = regions
        _, floating = _interacting(layer.merged(), anchor)
        return _pieces(floating, dbu, "not anchored: floats away at release")


class Release(_Kind):
    """The etch undercuts the layer from its edges (outlines and release
    holes) by ``undercut``. Away from anchors, every part must be undercut,
    so no part may be wider than twice the undercut; an anchored part must be
    wider than that, or the undercut frees it too.

    Widths are compared with an allowance of the process's ``grid`` (rounding
    onto it can make a slanted beam a few nm wider), so a beam exactly twice
    the undercut wide passes at any angle. Material held only along an
    anchor's edge (where a beam meets its anchor pad) is part of the anchor's
    foot, not a part that fails to release."""

    name = "release"
    title = "Release"
    roles = ("layer", "anchor")
    parameters = (Option("undercut", 1.0, "Undercut", minimum=0, suffix=" µm"),)

    def check(
        self, regions: Sequence[Region], dbu: float, undercut: float, grid: float = 0.001
    ) -> list[Finding]:
        layer, anchor = regions
        layer = layer.merged()
        shrink = _dbu(undercut, dbu)
        allowance = math.ceil(1.5 * grid / dbu)  # a slanted edge rounded onto the grid
        held = layer.sized(-(shrink + allowance))  # what the undercut does not reach
        foot = anchor.sized(shrink)
        # Held material only along an anchor's edge is part of its foot.
        stuck = [p for p in _real(held - anchor) if not (Region(p) - foot).is_empty()]
        limit = f"{2 * undercut:g} µm (2 × {undercut:g} µm undercut)"

        def message(width) -> str:
            size = f"{_um(width)} µm wide > {limit}" if width else f"wider than {limit}"
            return (
                f"not released: {size}; if it should move, narrow it or add release holes, "
                "if it should stay fixed (a pad, a wire, a frame), anchor it"
            )

        findings = _measured(stuck, dbu, message, shrink + allowance)
        anchors = layer & anchor
        _, lost = _interacting(anchors, anchors.sized(-shrink))
        return findings + _pieces(
            lost, dbu, f"anchor narrower than 2 × {undercut:g} µm: undercut frees it"
        )


BUILTIN = (
    MinWidth,
    MinSpace,
    MaxWidth,
    MinArea,
    MinHoleArea,
    Connected,
    Enclosure,
    Separation,
    Inside,
    NotOverlapping,
    Anchored,
    Release,
)
