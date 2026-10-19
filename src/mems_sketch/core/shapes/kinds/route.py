"""Route: a wire from one port to another, which follows them when they move.

``start`` and ``end`` name ports of the shapes beside it: a placed
component's (``pad1.port.signal``) or a path's or another route's
(``trace.port.end``). ``style`` says which way it goes:

* ``manhattan`` (the default): along x and y only, leaving the start and
  coming into the end along the ports' directions (which must be along x or
  y), with as few bends as it can and then as short as it can;
* ``l``: along the start's direction, then the end's, with one bend where
  the two lines meet;
* ``straight``: straight lines from the start through each via point to the
  end, whatever way the ports face.

``via`` are points it passes through, in order (not for ``l``). ``bend`` is
``sharp``, ``round`` (an exact arc of ``radius``, which must be more than
half the width) or ``chamfer`` (cut ``radius`` along each side). It is as
wide as its ports and on their layer unless ``width`` or ``layer`` is given;
ports of different widths need a width. It does not avoid other shapes: the
connectivity check reports what it shorts.

Its own ports ``start`` and ``end`` are where it ends, pointing out of it, so
another route or a connection can carry on from it.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field, field_validator

from mems_sketch.core.shapes.base import Node, Value, check_point_reference


class RouteShape(Node):
    category: ClassVar[str] = "reference"  # it runs between other shapes' ports
    icon: ClassVar[str] = "route"

    kind: Literal["route"] = "route"
    start: str
    end: str
    style: Literal["manhattan", "l", "straight"] = "manhattan"
    via: list[tuple[Value, Value]] = Field(default_factory=list)
    bend: Literal["sharp", "round", "chamfer"] = "sharp"
    radius: Value = 0.0
    width: Value = 0.0  # 0: the ports'
    layer: str | None = None  # default: its ports' layer

    @field_validator("start", "end")
    @classmethod
    def _port(cls, reference: str) -> str:
        check_point_reference(reference)
        if ".port." not in reference:
            raise ValueError(f"'{reference}' is not a port, like 'pad.port.signal'")
        return reference

    def point_references(self) -> list[str]:
        return [self.start, self.end]

    def moved(self, x, y, inner) -> dict:
        return {"via": [(x(px), y(py)) for px, py in self.via]}  # its ends are where its ports are

    def handles(self) -> list[tuple[Value, Value]]:
        return list(self.via)

    def with_handle(self, index: int, point: tuple[Value, Value]) -> RouteShape:
        via = list(self.via)
        via[index] = point
        return self.model_validate({**self.model_dump(), "via": via})

    def summary(self) -> str:
        return f"{self.style} route"

    def detail(self) -> str:
        return f"{self.start} → {self.end}"
