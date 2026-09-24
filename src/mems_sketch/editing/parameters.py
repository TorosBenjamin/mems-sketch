"""The parameters of the active component."""

from __future__ import annotations

from typing import Any

from mems_sketch.core.project import Project
from mems_sketch.core.user_component import ParamDef
from mems_sketch.editing.commands import Commands
from mems_sketch.editing.naming import fresh_name


class ParameterEdits(Commands):
    """The parameters of the active component."""

    def set(self, name: str, default: float | str, **limits: Any) -> None:
        def change(project: Project) -> None:
            self.session.local(project)
            project.set_parameter(name, default, self.session.active, **limits)

        self.session.edit(f"Set {name}", change)

    def update(self, name: str, /, **fields: Any) -> None:
        """Change any field of a parameter (name, default, min, max, integer, description)."""

        def change(project: Project) -> None:
            parameters = self.session.local(project).parameters
            for index, existing in enumerate(parameters):
                if existing.name == name:
                    updated = ParamDef.model_validate({**existing.model_dump(), **fields})
                    if updated.name != name and any(p.name == updated.name for p in parameters):
                        raise ValueError(f"parameter '{updated.name}' already exists")
                    parameters[index] = updated
                    return
            raise KeyError(name)

        self.session.edit(f"Edit parameter {name}", change)

    def set_internal(self, names: list[str], internal: bool = True) -> None:
        """Make parameters internal (only the component uses them) or public again."""

        def change(project: Project) -> None:
            parameters = self.session.local(project).parameters
            missing = set(names) - {p.name for p in parameters}
            if missing:
                raise KeyError(", ".join(sorted(missing)))
            parameters[:] = [
                p.model_copy(update={"internal": internal}) if p.name in names else p
                for p in parameters
            ]

        which = "internal" if internal else "public"
        self.session.edit(f"Make {', '.join(names)} {which}", change)

    def add(self) -> str:
        taken = {p.name for p in self.session.active_definition.parameters}
        name = fresh_name("param", taken)
        self.set(name, 0.0)
        return name

    def remove(self, name: str) -> None:
        def change(project: Project) -> None:
            self.session.local(project)
            project.remove_parameter(name, self.session.active)

        self.session.edit(f"Delete {name}", change)
