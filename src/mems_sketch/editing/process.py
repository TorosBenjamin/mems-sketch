"""Process constants, layers and design rules."""

from __future__ import annotations

import dataclasses

from mems_sketch.core.process import (
    Layer,
    Level,
    Rule,
    check_levels,
)
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
            base = project.base_process
            if base is not None and old in base.constants:
                raise ValueError(f"process.{old} belongs to process {project.process_name}")
            if not new.isidentifier():
                raise ValueError(f"'{new}' is not a valid constant name")
            if new in project.process.constants:
                raise ValueError(f"constant '{new}' already exists")
            project.process.constants = {
                (new if k == old else k): v for k, v in project.process.constants.items()
            }

        self.session.edit(f"Rename process.{old}", change)

    def remove_constant(self, name: str) -> None:
        def change(project: Project) -> None:
            base = project.base_process
            if base is not None and name in base.constants:
                raise ValueError(f"process.{name} belongs to process {project.process_name}")
            project.process.constants.pop(name)

        self.session.edit(f"Delete process.{name}", change)

    def add_constant(self) -> str:
        name = fresh_name("constant", set(self.session.project.process.constants))
        self.set_constant(name, 0.0)
        return name

    def set_layer(self, name: str, layer: Layer) -> None:
        def change(project: Project) -> None:
            _own_layers(project)
            if layer.name != name and layer.name in project.layers:
                raise ValueError(f"layer '{layer.name}' already exists")
            project.layers = {
                (layer.name if k == name else k): (layer if k == name else v)
                for k, v in project.layers.items()
            }
            if layer.name != name:  # the rules and the layer stack follow a renamed layer
                process = project.process
                process.levels = [
                    Level(
                        layer.name if lv.layer == name else lv.layer,
                        {r: layer.name if n == name else n for r, n in lv.roles.items()},
                    )
                    for lv in process.levels
                ]
                if process.default_level == name:
                    process.default_level = layer.name
                for rule in project.process.rules.values():
                    rule.layers = [layer.name if n == name else n for n in rule.layers]

        self.session.edit(f"Edit layer {name}", change)

    def add_layer(self) -> str:
        existing = self.session.project.layers
        name = fresh_name("layer", set(existing))
        gds = max((ly.gds_layer for ly in existing.values()), default=0) + 1

        def change(project: Project) -> None:
            _own_layers(project)
            project.add_layer(Layer(name, gds))

        self.session.edit(f"Add layer {name}", change)
        return name

    def remove_layer(self, name: str) -> None:
        def change(project: Project) -> None:
            _own_layers(project)
            project.layers.pop(name)
            process = project.process
            process.levels = [
                Level(lv.layer, {r: n for r, n in lv.roles.items() if n != name})
                for lv in process.levels
                if lv.layer != name
            ]
            if process.default_level == name:
                process.default_level = None

        self.session.edit(f"Delete layer {name}", change)

    def set_levels(self, levels: list[Level], default: str | None = None) -> None:
        """The layer stack, bottom to top, and the level a top component is on
        (None: the first)."""

        def change(project: Project) -> None:
            _own_layers(project)
            check_levels(levels, default, project.layers)
            project.process.levels = list(levels)
            project.process.default_level = default

        self.session.edit("Edit layer stack", change)

    # -- design rules --------------------------------------------------------

    def set_rule(self, name: str, rule: Rule) -> None:
        """Replace rule ``name`` with ``rule`` (which may rename it)."""

        def change(project: Project) -> None:
            rules = project.process.rules
            if rule.name != name and rule.name in rules:
                raise ValueError(f"rule '{rule.name}' already exists")
            base = project.base_process
            if rule.name != name and base is not None and name in base.rules:
                raise ValueError(
                    f"'{name}' is a rule of process {project.process_name}: keep its name"
                )
            if name in project.reasons and rule.name != name:
                project.reasons[rule.name] = project.reasons.pop(name)
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
        def change(project: Project) -> None:
            base = project.base_process
            if base is not None and name in base.rules:
                raise ValueError(
                    f"'{name}' is a rule of process {project.process_name}: turn it off instead"
                )
            project.process.rules.pop(name)
            project.reasons.pop(name, None)

        self.session.edit(f"Delete rule {name}", change)

    # -- the process used ------------------------------------------------------

    def use(self, name: str) -> None:
        """Use another process: one of the project's own, or a library's
        (``std.polymumps``), whose layers then stay as they are there."""
        self.session.edit(f"Use process {name}", lambda p: p.use_process(name))

    def set_reason(self, rule: str, reason: str) -> None:
        """Why a rule of a library's process is changed or added (empty: none)."""

        def change(project: Project) -> None:
            if project.base_process is None:
                raise ValueError("only changes to a library's process have reasons")
            if rule not in project.process.rules:
                raise KeyError(rule)
            if reason.strip():
                project.reasons[rule] = reason.strip()
            else:
                project.reasons.pop(rule, None)

        self.session.edit(f"Reason for {rule}", change)

    def reset_rule(self, name: str) -> None:
        """A rule of a library's process back to the process's own."""

        def change(project: Project) -> None:
            base = project.base_process
            if base is None or name not in base.rules:
                raise ValueError(f"'{name}' is not a rule of a library's process")
            project.process.rules[name] = dataclasses.replace(base.rules[name])
            project.reasons.pop(name, None)

        self.session.edit(f"Reset {name}", change)

    # -- waivers -------------------------------------------------------------

    def waive(self, violation, reason: str, component: str | None = None) -> None:
        """Accept one violation of the active (or given) component, for ``reason``
        (requirement DRC-13). It is kept with the component and lapses when the
        geometry around it changes."""
        from mems_sketch.core.user_component import Waiver
        from mems_sketch.process.rules import fingerprint

        component = component or self.session.active
        if not reason.strip():
            raise ValueError("a waiver needs a reason")
        if violation.bbox_um is None:
            raise ValueError("only a violation with a place can be waived")
        geometry = self.session.results.geometry(component=component)
        waiver = Waiver(
            rule=violation.rule,
            box=tuple(violation.bbox_um),
            reason=reason.strip(),
            fingerprint=fingerprint(violation, geometry),
        )

        def change(project) -> None:
            definition = project.components.get(component)
            if definition is None:
                raise ValueError(f"'{component}' is read-only: its violations cannot be waived")
            definition.waivers = [
                w for w in definition.waivers if (w.rule, w.box) != (waiver.rule, waiver.box)
            ] + [waiver]

        self.session.edit(f"Waive {violation.rule}", change)

    def unwaive(self, rule: str, box, component: str | None = None) -> None:
        """Remove the waiver of ``rule`` at ``box``."""
        from mems_sketch.process.rules import BOX_TOLERANCE_UM

        component = component or self.session.active

        def matches(waiver) -> bool:
            return waiver.rule == rule and all(
                abs(p - q) <= BOX_TOLERANCE_UM for p, q in zip(waiver.box, box, strict=True)
            )

        def change(project) -> None:
            definition = project.components[component]
            kept = [w for w in definition.waivers if not matches(w)]
            if len(kept) == len(definition.waivers):
                raise ValueError(f"no waiver of {rule} there")
            definition.waivers = kept

        self.session.edit(f"Remove the waiver of {rule}", change)


def _own_layers(project: Project) -> None:
    """Layers and the layer stack can change only in a process of the project's own."""
    if project.base_process is not None:
        raise ValueError(
            f"the layers belong to process {project.process_name}: "
            "use a process of the project's own to change them"
        )
