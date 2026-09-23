"""The declared points of the active component."""

from __future__ import annotations

from typing import Any

from mems_sketch.core.project import Project
from mems_sketch.core.user_component import PointDef
from mems_sketch.editing.commands import Commands
from mems_sketch.editing.naming import fresh_name


class PointEdits(Commands):
    """The declared points of the active component."""

    def add(self) -> str:
        name = fresh_name("point", {p.name for p in self.session.active_definition.points})
        self.session.edit(
            f"Add point {name}",
            lambda p: self.session.local(p).points.append(PointDef(name=name)),
        )
        return name

    def update(self, name: str, /, **fields: Any) -> None:
        """Change any field of a declared point (name, at, x, y, description)."""

        def change(project: Project) -> None:
            points = self.session.local(project).points
            for index, existing in enumerate(points):
                if existing.name == name:
                    updated = PointDef.model_validate({**existing.model_dump(), **fields})
                    if updated.name != name and any(p.name == updated.name for p in points):
                        raise ValueError(f"point '{updated.name}' already exists")
                    points[index] = updated
                    return
            raise KeyError(name)

        self.session.edit(f"Edit point {name}", change)

    def remove(self, name: str) -> None:
        def change(project: Project) -> None:
            definition = self.session.local(project)
            definition.points = [p for p in definition.points if p.name != name]

        self.session.edit(f"Delete point {name}", change)
