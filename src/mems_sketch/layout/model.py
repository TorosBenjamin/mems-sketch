"""A layout in memory: cells with polygons per GDS layer and placements of
other cells, in integer database units (``dbu`` µm each)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from mems_sketch.core.region import IntPolygon

GdsLayer = tuple[int, int]  # layer, datatype
MAX_POINTS = 8000  # per polygon: what GDS readers take


@dataclass
class Placement:
    """A cell placed in another: mirrored about the x axis (``mirror``), then
    rotated by ``angle`` degrees and scaled by ``mag``, then moved to ``x``,
    ``y`` (database units). ``columns`` × ``rows`` copies, each column
    ``column_step`` and each row ``row_step`` further on (vectors in the
    parent's frame)."""

    cell: str
    x: int = 0
    y: int = 0
    angle: float = 0.0
    mirror: bool = False
    mag: float = 1.0
    columns: int = 1
    rows: int = 1
    column_step: tuple[int, int] = (0, 0)
    row_step: tuple[int, int] = (0, 0)

    @property
    def quarter_turns(self) -> int | None:
        """The rotation in quarter turns, if it is one and the copy is not scaled."""
        turns = self.angle / 90
        if self.mag != 1 or abs(turns - round(turns)) > 1e-9:
            return None
        return round(turns) % 4

    def copies(self) -> list[tuple[int, int]]:
        """Where each copy's origin goes."""
        return [
            (
                self.x + i * self.column_step[0] + j * self.row_step[0],
                self.y + i * self.column_step[1] + j * self.row_step[1],
            )
            for j in range(self.rows)
            for i in range(self.columns)
        ]

    def apply(self, points, at: tuple[int, int]) -> np.ndarray:
        """``points`` (an (n, 2) array) of the placed cell, in the parent's frame,
        for the copy at ``at``."""
        x = points[:, 0].astype(float)
        y = -points[:, 1].astype(float) if self.mirror else points[:, 1].astype(float)
        turns = self.quarter_turns
        if turns is not None:  # exact
            c, s = ((1, 0), (0, 1), (-1, 0), (0, -1))[turns]
            mag = 1
        else:
            c, s = math.cos(math.radians(self.angle)), math.sin(math.radians(self.angle))
            mag = self.mag
        out = np.empty((len(points), 2), dtype=np.int64)
        out[:, 0] = np.rint(mag * (c * x - s * y)) + at[0]
        out[:, 1] = np.rint(mag * (s * x + c * y)) + at[1]
        return out[::-1].copy() if self.mirror else out  # keep the winding


@dataclass
class Cell:
    name: str
    polygons: dict[GdsLayer, list[IntPolygon]] = field(default_factory=dict)
    placements: list[Placement] = field(default_factory=list)

    def add(self, layer: GdsLayer, polygon: IntPolygon) -> None:
        self.polygons.setdefault(layer, []).append(polygon)


@dataclass
class Layout:
    dbu: float = 0.001  # µm per database unit
    cells: dict[str, Cell] = field(default_factory=dict)
    layer_names: dict[GdsLayer, str] = field(default_factory=dict)

    def cell(self, name: str) -> Cell:
        if name not in self.cells:
            self.cells[name] = Cell(name)
        return self.cells[name]

    def top_cells(self) -> list[str]:
        placed = {p.cell for cell in self.cells.values() for p in cell.placements}
        return [name for name in self.cells if name not in placed]

    def layers_in(self, name: str, seen: frozenset[str] = frozenset()) -> set[GdsLayer]:
        """The layers with polygons in a cell or the cells it places."""
        if name in seen:
            raise ValueError(f"cell '{name}' places itself")
        cell = self.cells[name]
        found = {layer for layer, polygons in cell.polygons.items() if polygons}
        for placement in cell.placements:
            if placement.cell in self.cells:
                found |= self.layers_in(placement.cell, seen | {name})
        return found

    def flat(
        self, name: str, layer: GdsLayer, seen: frozenset[str] = frozenset()
    ) -> list[IntPolygon]:
        """A cell's polygons on ``layer`` with every placement in it flattened."""
        if name in seen:
            raise ValueError(f"cell '{name}' places itself")
        cell = self.cells[name]
        result = list(cell.polygons.get(layer, []))
        for placement in cell.placements:
            if placement.cell not in self.cells:
                continue
            inner = self.flat(placement.cell, layer, seen | {name})
            for at in placement.copies():
                for polygon in inner:
                    result.append(
                        IntPolygon(
                            placement.apply(polygon.hull, at),
                            [placement.apply(h, at) for h in polygon.holes],
                        )
                    )
        return result


def hole_free(polygon: IntPolygon, max_points: int = MAX_POINTS) -> list[IntPolygon]:
    """``polygon`` as pieces without holes and with at most ``max_points``
    points each: each hole joined to the outline by a zero-width cut (no new
    points, so the pieces fill exactly what the polygon did), and a polygon
    with too many points split along grid lines first."""
    if not polygon.holes and len(polygon.hull) <= max_points:
        return [polygon]
    from mems_sketch import _geom

    pieces = _geom.grid_hole_free([(polygon.hull, polygon.holes)], max_points)
    return [IntPolygon(ring) for ring in pieces]
