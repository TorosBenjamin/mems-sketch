"""Connection: the area that joins beam ends, made from their ports.

``ends`` names two or more ports of the shapes beside it: a placed
component's (``spring1.port.end``) or a path's (``beam.port.start``). Taken in
order around their centre, each neighbour's sides run on until they meet, and
the corner is rounded by ``inner_radius`` where it turns in between the beams
(the usual case) or ``radius`` where it turns out; where the sides meet behind
an end, or never, the gap is closed straight. The beams are not changed: the
connection is drawn beside them, on their layer (or ``layer``), reaching a
little way into each.

It is an error, naming the ends, when an end is not on its shape's end (no
material behind it all the way across: off the shape, facing the wrong way,
on a rounded corner, wider than the beam), points away from the others, has
no width (a port without one takes ``width``), when the ends are on
different layers and no ``layer`` is given, or when a radius does not fit
between two ends (the error says the largest that does).
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field, field_validator

from mems_sketch.core.shapes.base import Node, Value, check_point_reference


class ConnectionShape(Node):
    category: ClassVar[str] = "reference"  # it joins other shapes' ends
    icon: ClassVar[str] = "connection"

    kind: Literal["connection"] = "connection"
    ends: list[str] = Field(default_factory=list)
    inner_radius: Value = 0.0
    radius: Value = 0.0
    width: Value = 0.0  # for an end whose port has none
    layer: str | None = None  # default: its ends' layer

    @field_validator("ends")
    @classmethod
    def _ends(cls, ends: list[str]) -> list[str]:
        for end in ends:
            if ".port." not in end:
                raise ValueError(f"'{end}' is not a port, like 'beam.port.end'")
            check_point_reference(end)
        return ends

    def point_references(self) -> list[str]:
        return list(self.ends)

    def moved(self, x, y, inner) -> dict:
        return {}  # it is where its ends are

    def summary(self) -> str:
        count = len(self.ends)
        return f"connection of {count} end{'s' if count != 1 else ''}"

    def detail(self) -> str:
        shapes = sorted({end.partition(".")[0] for end in self.ends})
        return ", ".join(shapes) or "no ends"

    @classmethod
    def default(cls, layer: str) -> ConnectionShape:
        return cls()
