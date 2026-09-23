"""Process constants and layers."""

from __future__ import annotations

from mems_sketch.core.process import Layer
from mems_sketch.core.project import Project
from mems_sketch.editing.commands import Commands
from mems_sketch.editing.naming import fresh_name


class ProcessEdits(Commands):
    """Process constants and layers."""

    def set_constant(self, name: str, value: float | str) -> None:
        def change(project: Project) -> None:
            if not name.isidentifier():
                raise ValueError(f"'{name}' is not a valid constant name")
            project.process.constants[name] = value
            project.process.scope()

        self.session.edit(f"Set process.{name}", change)

    def rename_constant(self, old: str, new: str) -> None:
        def change(project: Project) -> None:
            if not new.isidentifier():
                raise ValueError(f"'{new}' is not a valid constant name")
            if new in project.process.constants:
                raise ValueError(f"constant '{new}' already exists")
            project.process.constants = {
                (new if k == old else k): v for k, v in project.process.constants.items()
            }

        self.session.edit(f"Rename process.{old}", change)

    def remove_constant(self, name: str) -> None:
        self.session.edit(f"Delete process.{name}", lambda p: p.process.constants.pop(name))

    def add_constant(self) -> str:
        name = fresh_name("constant", set(self.session.project.process.constants))
        self.set_constant(name, 0.0)
        return name

    def set_layer(self, name: str, layer: Layer) -> None:
        def change(project: Project) -> None:
            if layer.name != name and layer.name in project.layers:
                raise ValueError(f"layer '{layer.name}' already exists")
            project.layers = {
                (layer.name if k == name else k): (layer if k == name else v)
                for k, v in project.layers.items()
            }

        self.session.edit(f"Edit layer {name}", change)

    def add_layer(self) -> str:
        existing = self.session.project.layers
        name = fresh_name("layer", set(existing))
        gds = max((ly.gds_layer for ly in existing.values()), default=0) + 1
        self.session.edit(f"Add layer {name}", lambda p: p.add_layer(Layer(name, gds)))
        return name

    def remove_layer(self, name: str) -> None:
        self.session.edit(f"Delete layer {name}", lambda p: p.layers.pop(name))
