"""The built-in rule kinds (requirement DRC-11). Each is a plugin like any
other (see :mod:`mems_sketch.process.rules`): a name, the layers it takes by
role, its parameters, and a check on KLayout regions in database units."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import ClassVar

import klayout.db as kdb

from mems_sketch.options import Option
from mems_sketch.process.rules import Finding

DISTANCE = Option("value", 1.0, "Distance", minimum=0, suffix=" µm")


def _box(box: kdb.Box, dbu: float) -> tuple[float, float, float, float]:
    return (box.left * dbu, box.bottom * dbu, box.right * dbu, box.top * dbu)


def _pairs(pairs: kdb.EdgePairs, dbu: float, message: str) -> list[Finding]:
    return [Finding(message, _box(p.bbox(), dbu)) for p in pairs.each()]


def _pieces(polygons: Iterable[kdb.Polygon], dbu: float, message: str) -> list[Finding]:
    return [Finding(message, _box(p.bbox(), dbu)) for p in polygons]


def _dbu(value_um: float, dbu: float) -> int:
    return round(value_um / dbu)


class _Kind:
    name: ClassVar[str]
    title: ClassVar[str]
    roles: ClassVar[tuple[str, ...]] = ("layer",)
    parameters: ClassVar[tuple[Option, ...]] = (DISTANCE,)


# -- one layer -----------------------------------------------------------------


class MinWidth(_Kind):
    name = "min_width"
    title = "Minimum width"

    def check(self, regions: Sequence[kdb.Region], dbu: float, value: float) -> list[Finding]:
        pairs = regions[0].width_check(_dbu(value, dbu))
        return _pairs(pairs, dbu, f"width < {value:g} µm")


class MinSpace(_Kind):
    name = "min_space"
    title = "Minimum spacing"

    def check(self, regions: Sequence[kdb.Region], dbu: float, value: float) -> list[Finding]:
        pairs = regions[0].space_check(_dbu(value, dbu))
        return _pairs(pairs, dbu, f"spacing < {value:g} µm")


class MaxWidth(_Kind):
    name = "max_width"
    title = "Maximum width"

    def check(self, regions: Sequence[kdb.Region], dbu: float, value: float) -> list[Finding]:
        # What survives shrinking by half the width is wider than the width.
        wide = regions[0].merged().sized(-(_dbu(value, dbu) // 2 + 1))
        return _pieces(wide.each_merged(), dbu, f"wider than {value:g} µm")


class MinArea(_Kind):
    name = "min_area"
    title = "Minimum area"
    parameters = (Option("value", 1.0, "Area", minimum=0, suffix=" µm²"),)

    def check(self, regions: Sequence[kdb.Region], dbu: float, value: float) -> list[Finding]:
        limit = math.ceil(value / dbu**2)
        small = regions[0].merged().with_area(0, limit, False)
        return _pieces(small.each(), dbu, f"area < {value:g} µm²")


class MinHoleArea(_Kind):
    name = "min_hole_area"
    title = "Minimum hole area"
    parameters = (Option("value", 1.0, "Area", minimum=0, suffix=" µm²"),)

    def check(self, regions: Sequence[kdb.Region], dbu: float, value: float) -> list[Finding]:
        limit = math.ceil(value / dbu**2)
        small = regions[0].merged().holes().with_area(0, limit, False)
        return _pieces(small.each(), dbu, f"hole area < {value:g} µm²")


class Connected(_Kind):
    name = "connected"
    title = "Number of pieces"
    parameters = (Option("pieces", 1, "Pieces", minimum=1),)

    def check(self, regions: Sequence[kdb.Region], dbu: float, pieces: int) -> list[Finding]:
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

    def check(self, regions: Sequence[kdb.Region], dbu: float, value: float) -> list[Finding]:
        outer, inner = regions
        findings = _pairs(
            outer.enclosing_check(inner, _dbu(value, dbu)),
            dbu,
            f"enclosed by less than {value:g} µm",
        )
        return findings + _pieces((inner - outer).each(), dbu, "not enclosed")


class Separation(_Kind):
    name = "separation"
    title = "Separation"
    roles = ("first", "second")

    def check(self, regions: Sequence[kdb.Region], dbu: float, value: float) -> list[Finding]:
        first, second = regions
        pairs = first.separation_check(second, _dbu(value, dbu))
        return _pairs(pairs, dbu, f"closer than {value:g} µm") + _pieces(
            (first & second).each(), dbu, "overlapping"
        )


class Inside(_Kind):
    name = "inside"
    title = "Inside"
    roles = ("inner", "outer")
    parameters = ()

    def check(self, regions: Sequence[kdb.Region], dbu: float) -> list[Finding]:
        inner, outer = regions
        return _pieces((inner - outer).each(), dbu, "outside")


class NotOverlapping(_Kind):
    name = "not_overlapping"
    title = "Not overlapping"
    roles = ("first", "second")
    parameters = ()

    def check(self, regions: Sequence[kdb.Region], dbu: float) -> list[Finding]:
        first, second = regions
        return _pieces((first & second).each(), dbu, "overlapping")


# -- MEMS topology ---------------------------------------------------------------


class Anchored(_Kind):
    name = "anchored"
    title = "Anchored"
    roles = ("layer", "anchor")
    parameters = ()

    def check(self, regions: Sequence[kdb.Region], dbu: float) -> list[Finding]:
        layer, anchor = regions
        floating = layer.merged().not_interacting(anchor)
        return _pieces(floating.each(), dbu, "not anchored: floats away at release")


class Release(_Kind):
    """The etch undercuts the layer from its edges (outlines and release
    holes) by ``undercut``. Away from anchors, every part must be undercut,
    so no part may be wider than twice the undercut; an anchored part must be
    wider than that, or the undercut frees it too."""

    name = "release"
    title = "Release"
    roles = ("layer", "anchor")
    parameters = (Option("undercut", 1.0, "Undercut", minimum=0, suffix=" µm"),)

    def check(self, regions: Sequence[kdb.Region], dbu: float, undercut: float) -> list[Finding]:
        layer, anchor = regions
        layer = layer.merged()
        shrink = _dbu(undercut, dbu)
        held = layer.sized(-shrink)  # what the undercut does not reach
        stuck = held - anchor
        findings = _pieces(
            stuck.each_merged(),
            dbu,
            f"not released: wider than 2 × {undercut:g} µm undercut (add release holes)",
        )
        anchors = layer & anchor
        lost = anchors.merged().not_interacting(anchors.sized(-shrink))
        return findings + _pieces(
            lost.each(), dbu, f"anchor narrower than 2 × {undercut:g} µm: undercut frees it"
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
