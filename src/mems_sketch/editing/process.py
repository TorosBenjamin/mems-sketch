"""Process constants, layers and design rules."""

from __future__ import annotations

import dataclasses

from mems_sketch.core.process import Layer, Rule
from mems_sketch.core.project import Project
from mems_sketch.editing.commands import Commands
from mems_sketch.editing.naming import fresh_name


class ProcessEdits(Commands):
    """Process constants, layers and design rules."""

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
            if layer.name != name:  # the rules follow a renamed layer
                for rule in project.process.rules.values():
                    rule.layers = [layer.name if n == name else n for n in rule.layers]

        self.session.edit(f"Edit layer {name}", change)

    def add_layer(self) -> str:
        existing = self.session.project.layers
        name = fresh_name("layer", set(existing))
        gds = max((ly.gds_layer for ly in existing.values()), default=0) + 1
        self.session.edit(f"Add layer {name}", lambda p: p.add_layer(Layer(name, gds)))
        return name

    def remove_layer(self, name: str) -> None:
        self.session.edit(f"Delete layer {name}", lambda p: p.layers.pop(name))

    # -- design rules --------------------------------------------------------

    def set_rule(self, name: str, rule: Rule) -> None:
        """Replace rule ``name`` with ``rule`` (which may rename it)."""

        def change(project: Project) -> None:
            rules = project.process.rules
            if rule.name != name and rule.name in rules:
                raise ValueError(f"rule '{rule.name}' already exists")
            if not rule.name.strip():
                raise ValueError("a rule needs a name")
            project.process.rules = {
                (rule.name if k == name else k): (rule if k == name else v)
                for k, v in rules.items()
            }

        self.session.edit(f"Edit rule {name}", change)

    def enable_rule(self, name: str, enabled: bool) -> None:
        rule = self.session.project.process.rules[name]
        self.set_rule(name, dataclasses.replace(rule, enabled=enabled))

    def add_rule(self, kind: str) -> str:
        """A new rule of ``kind``, on the first layers, with the kind's defaults."""
        from mems_sketch.process.rules import available_rule_kinds

        cls = available_rule_kinds()[kind]
        layers = list(self.session.project.layers)
        if len(layers) < len(cls.roles):
            raise ValueError(f"'{kind}' needs {len(cls.roles)} layers")
        chosen = layers[: len(cls.roles)]
        taken = set(self.session.project.process.rules)
        name = f"{chosen[0]}_{kind}"
        if name in taken:
            name = fresh_name(f"{name}_2", taken)
        values = {p.name: p.default for p in cls.parameters}
        rule = Rule(name, kind, chosen, values)
        self.session.edit(f"Add rule {name}", lambda p: p.process.add_rule(rule))
        return name

    def remove_rule(self, name: str) -> None:
        self.session.edit(f"Delete rule {name}", lambda p: p.process.rules.pop(name))
