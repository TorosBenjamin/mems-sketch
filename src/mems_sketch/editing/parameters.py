"""The parameters of the active component."""

from __future__ import annotations

import math
from typing import Any

from mems_sketch.core.project import Project
from mems_sketch.core.user_component import ParamDef
from mems_sketch.editing.commands import Commands
from mems_sketch.editing.naming import fresh_name


def with_limits(fields: dict[str, Any]) -> dict[str, Any]:
    """``min: "> 20"`` and ``max: "< size / 2"`` as the Parameters panel writes them:
    the limit, exclusive. The GUI, the MCP server and scripts take the same forms
    (#311)."""
    fields = dict(fields)
    for key, mark in (("min", ">"), ("max", "<")):
        value = fields.get(key)
        if isinstance(value, str) and value.strip().startswith(mark):
            text = value.strip().removeprefix(mark).strip()
            if not text:
                raise ValueError(f"{key} '{value}': a value is needed after '{mark}'")
            fields[key] = _number(text)
            fields[f"{key}_exclusive"] = True
    return fields


def _number(text: str) -> float | str:
    """A number if ``text`` is a finite one, else the text as an expression."""
    try:
        value = float(text)
    except ValueError:
        return text
    return value if math.isfinite(value) else text


class ParameterEdits(Commands):
    """The parameters of the active component."""

    def set(self, name: str, default: float | str, **limits: Any) -> None:
        limits = with_limits(limits)

        def change(project: Project) -> None:
            self.session.local(project)
            project.set_parameter(name, default, self.session.active, **limits)

        self.session.edit(f"Set {name}", change)

    def update(self, name: str, /, **fields: Any) -> None:
        """Change any field of a parameter (name, default, min, max, integer,
        description). A new name is followed by the expressions that use it and
        by the placements that pass it a value."""
        new = fields.pop("name", name)
        fields = with_limits(fields)

        def change(project: Project) -> None:
            self.session.local(project)
            if new != name:
                ParamDef(name=new)  # a valid name, or why not
                project.rename_parameter(self.session.active, name, new)
            parameters = self.session.local(project).parameters
            for index, existing in enumerate(parameters):
                if existing.name == new:
                    parameters[index] = ParamDef.model_validate({**existing.model_dump(), **fields})
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
