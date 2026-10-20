"""The ports of the active component."""

from __future__ import annotations

from typing import Any

from mems_sketch.core.project import Project
from mems_sketch.core.user_component import PortDef
from mems_sketch.editing.commands import Commands
from mems_sketch.editing.naming import fresh_name


class PortEdits(Commands):
    """The ports of the active component: where wires attach."""

    def add(self, at: str | None = None, **fields: Any) -> str:
        """A new port, at ``at`` (``shape.point``, a placed component's port
        ``pad.port.signal``, or a point of the whole component) when given."""
        name = fields.pop("name", None) or fresh_name(
            "port", {p.name for p in self.session.active_definition.ports}
        )
        if any(p.name == name for p in self.session.active_definition.ports):
            raise ValueError(f"port '{name}' already exists")
        port = PortDef(name=name, at=at, **fields)
        self.session.edit(f"Add port {name}", lambda p: self.session.local(p).ports.append(port))
        return name

    def update(self, name: str, /, **fields: Any) -> None:
        """Change any field of a port (name, at, x, y, direction, width, layer,
        description). A new name is followed by the components that place this one."""

        new = fields.pop("name", name)

        def change(project: Project) -> None:
            if new != name:
                project.rename_port(self.session.active, name, new)
            ports = self.session.local(project).ports
            for index, existing in enumerate(ports):
                if existing.name == new:
                    ports[index] = PortDef.model_validate({**existing.model_dump(), **fields})
                    return
            raise KeyError(name)

        self.session.edit(f"Edit port {name}", change)

    def remove(self, name: str) -> None:
        def change(project: Project) -> None:
            definition = self.session.local(project)
            definition.ports = [p for p in definition.ports if p.name != name]

        self.session.edit(f"Delete port {name}", change)
