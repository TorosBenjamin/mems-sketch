"""Process constants, layers and design rules."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from mems_sketch.core.process import RULE_FIELDS, DeckUse, Layer, Rule, RuleDeck, RuleOverride
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
                for use in project.process.decks.values():  # deck rules by an override
                    for rule_name in use.deck.rules:
                        rule = use.rule(rule_name)
                        if name in rule.layers:
                            override = use.overrides.setdefault(rule_name, RuleOverride())
                            override.changes["layers"] = [
                                layer.name if n == name else n for n in rule.layers
                            ]
                            override.reason = override.reason or f"layer renamed to {layer.name}"

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

    # -- rule decks ----------------------------------------------------------

    def use_deck(self, path: str | Path, name: str | None = None) -> str:
        """Use the rule deck in the file at ``path``, as ``name`` (default: the
        deck's own name)."""
        from mems_sketch.storage.project_files import load_deck

        deck = load_deck(path)
        name = name or deck.name
        if not name.isidentifier():
            raise ValueError(f"'{name}' is not a valid rule deck name")
        if name in self.session.project.process.decks:
            raise ValueError(f"a rule deck named '{name}' is already used")
        use = DeckUse(deck=deck)
        self.session.edit(f"Use rule deck {name}", lambda p: p.process.decks.__setitem__(name, use))
        return name

    def remove_deck(self, name: str) -> None:
        self.session.edit(f"Stop using rule deck {name}", lambda p: p.process.decks.pop(name))

    def reload_deck(self, name: str) -> None:
        """Read the deck's file again, keeping the project's settings and overrides."""
        from mems_sketch.storage.project_files import load_deck

        use = self.session.project.process.decks[name]
        if use.deck.path is None:
            raise ValueError(f"rule deck '{name}' has no file")
        deck = load_deck(use.deck.path)

        def change(project) -> None:
            current = project.process.decks[name]
            current.deck, current.error = deck, ""

        self.session.edit(f"Reload rule deck {name}", change)

    def set_deck_parameter(self, deck: str, name: str, value: float | str | None) -> None:
        """Set a deck parameter for this project; None goes back to the deck's value."""

        def change(project) -> None:
            use = project.process.decks[deck]
            if name not in use.deck.parameters:
                known = ", ".join(use.deck.parameters) or "none"
                raise ValueError(f"rule deck '{deck}' has no parameter '{name}' (its: {known})")
            if value is None:
                use.parameters.pop(name, None)
            else:
                use.parameters[name] = value
            project.process.decks[deck].variables(project.process.scope())

        self.session.edit(f"Set {deck}.{name}", change)

    def override_rule(
        self, deck: str, rule: str, changes: dict[str, Any], reason: str | None = None
    ) -> None:
        """Change fields or values of a deck rule for this project. Changes back
        to the deck's own value are dropped; an override with none left goes."""

        def change(project) -> None:
            use = project.process.decks[deck]
            if rule not in use.deck.rules:
                raise ValueError(f"rule deck '{deck}' has no rule '{rule}'")
            original = use.deck.rules[rule]
            current = use.overrides.get(rule, RuleOverride())
            merged = {**current.changes, **changes}
            kept = {}
            for key, value in merged.items():
                if key == "kind":
                    raise ValueError("an override cannot change a rule's kind")
                own = getattr(original, key) if key in RULE_FIELDS else original.values.get(key)
                if value != own:
                    kept[key] = value
            if kept:
                use.overrides[rule] = RuleOverride(
                    kept, current.reason if reason is None else reason
                )
            else:
                use.overrides.pop(rule, None)

        self.session.edit(f"Override {deck}.{rule}", change)

    def reset_rule(self, deck: str, rule: str) -> None:
        """Back to the deck's own rule."""
        self.session.edit(
            f"Reset {deck}.{rule}", lambda p: p.process.decks[deck].overrides.pop(rule, None)
        )

    def save_rules_as_deck(
        self, path: str | Path, name: str, parameters: dict[str, float | str] | None = None
    ) -> Path:
        """Write the project's own rules as a rule deck file, for other projects."""
        from mems_sketch.storage.project_files import save_deck

        rules = {n: dataclasses.replace(r) for n, r in self.session.project.process.rules.items()}
        return save_deck(RuleDeck(name=name, parameters=dict(parameters or {}), rules=rules), path)

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
