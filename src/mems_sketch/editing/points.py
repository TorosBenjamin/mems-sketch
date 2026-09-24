"""The declared points of the active component."""

from __future__ import annotations

from typing import Any

from mems_sketch.core.project import Project
from mems_sketch.core.user_component import PointDef
from mems_sketch.editing.commands import Commands
from mems_sketch.editing.naming import fresh_name


class PointEdits(Commands):
    """The declared points of the active component."""

    def add(self, at: str | None = None) -> str:
        """A new declared point, measured from ``at`` (``shape.point``, or a point of
        the whole component such as ``center``) when given, so it sits right there."""
        base = at.replace(".", "_") if at else "point"
        name = fresh_name(base, {p.name for p in self.session.active_definition.points})
        self.session.edit(
            f"Add point {name}",
            lambda p: self.session.local(p).points.append(PointDef(name=name, at=at)),
        )
        return name

    def update(self, name: str, /, **fields: Any) -> None:
        """Change any field of a declared point (name, at, x, y, description).

        A new name is followed by the components that place this one."""

        new = fields.pop("name", name)

        def change(project: Project) -> None:
            if new != name:
                project.rename_point(self.session.active, name, new)
            points = self.session.local(project).points
            for index, existing in enumerate(points):
                if existing.name == new:
                    points[index] = PointDef.model_validate({**existing.model_dump(), **fields})
                    return
            raise KeyError(name)

        self.session.edit(f"Edit point {name}", change)

    def remove(self, name: str) -> None:
        def change(project: Project) -> None:
            definition = self.session.local(project)
            definition.points = [p for p in definition.points if p.name != name]

        self.session.edit(f"Delete point {name}", change)
