"""What changed between two versions of a project: in words, and in geometry.

:func:`diff_projects` lists the changes a person would describe: a component
added, a parameter's default from 20 to 25, a shape moved, a layer's GDS
number. Shapes are matched by name; unnamed ones by their content and
position in their list, so an inserted shape is one addition, not a change to
every shape after it. :func:`geometry_changes` is the material added and
removed, layer by layer.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

import klayout.db as kdb

from mems_sketch.core.component import Geometry
from mems_sketch.core.project import Project
from mems_sketch.core.shapes import NodePath, Shape
from mems_sketch.core.user_component import ComponentDef

ADDED, REMOVED, CHANGED = "added", "removed", "changed"
PROJECT, PROCESS, IMPORTS = "Project", "Process", "Imports"  # groups besides the components
MAX_VALUE_CHARS = 48


@dataclass(frozen=True)
class Change:
    action: str  # ADDED, REMOVED or CHANGED
    group: str  # the component's name, or PROJECT, PROCESS or IMPORTS
    what: str  # "component", "description", "parameter", "point", "shape", "layer", ...
    subject: str  # its name: "pitch", "cut › slot", "metal"
    details: tuple[str, ...] = ()  # what changed about it: "default 20 → 25"
    component: str | None = None  # the component to open to see it
    path: NodePath | None = None  # the shape to select there (the nearest one left)

    @property
    def text(self) -> str:
        """One line: ``parameter pitch: default 20 → 25``."""
        head = f"{self.what} {self.subject}".strip()
        if self.action != CHANGED:
            head = f"{head} {self.action}"
        return f"{head}: {'; '.join(self.details)}" if self.details else head


def diff_projects(old: Project | None, new: Project) -> list[Change]:
    """Everything that changed from ``old`` (None: nothing yet) to ``new``."""
    old = old or Project(top=None)
    changes: list[Change] = []
    changes += _project(old, new)
    changes += _process(old, new)
    changes += _imports(old, new)
    for name in _union(old.components, new.components):
        changes += _component(name, old.components.get(name), new.components.get(name))
    return changes


def geometry_changes(old: Geometry | None, new: Geometry | None) -> tuple[Geometry, Geometry]:
    """The material ``new`` has that ``old`` has not (added), and the reverse
    (removed), per layer; layers without a difference are left out."""
    added, removed = Geometry(), Geometry()
    old_layers = old.layers if old is not None else {}
    new_layers = new.layers if new is not None else {}
    for layer in _union(old_layers, new_layers):
        before = old_layers.get(layer, kdb.Region())
        after = new_layers.get(layer, kdb.Region())
        plus, minus = after - before, before - after
        if not plus.is_empty():
            added.layers[layer] = plus.merged()
        if not minus.is_empty():
            removed.layers[layer] = minus.merged()
    return added, removed


# -- the project, process and imports --------------------------------------------


def _project(old: Project, new: Project) -> list[Change]:
    details = _fields({"name": old.name, "top": old.top}, {"name": new.name, "top": new.top})
    changes = [Change(CHANGED, PROJECT, "project", "", details)] if details else []
    for name in _union(old.libraries, new.libraries):
        if name not in old.libraries or name not in new.libraries:
            action = ADDED if name in new.libraries else REMOVED
            changes.append(Change(action, PROJECT, "library", name))
    return changes


def _process(old: Project, new: Project) -> list[Change]:
    changes = []
    old_layers, new_layers = old.process.layers, new.process.layers
    for name in _union(old_layers, new_layers):
        before, after = old_layers.get(name), new_layers.get(name)
        if before is None or after is None:
            layer = after or before
            gds = f"GDS {layer.gds_layer}/{layer.gds_datatype}"
            changes.append(
                Change(ADDED if before is None else REMOVED, PROCESS, "layer", name, (gds,))
            )
            continue
        details = _fields(_layer_data(before), _layer_data(after))
        if details:
            changes.append(Change(CHANGED, PROCESS, "layer", name, details))
    before, after = old.process.constants, new.process.constants
    for name in _union(before, after):
        if name not in before or name not in after:
            value = _short(after.get(name, before.get(name)))
            changes.append(
                Change(ADDED if name in after else REMOVED, PROCESS, "constant", name, (value,))
            )
        elif before[name] != after[name]:
            detail = f"{_short(before[name])} → {_short(after[name])}"
            changes.append(Change(CHANGED, PROCESS, "constant", name, (detail,)))
    return changes


def _layer_data(layer) -> dict[str, Any]:
    data = dataclasses.asdict(layer)
    data["gds"] = f"{data.pop('gds_layer')}/{data.pop('gds_datatype')}"
    data.pop("name")
    return data


def _imports(old: Project, new: Project) -> list[Change]:
    changes = []
    for name in _union(old.imports, new.imports):
        before, after = old.imports.get(name), new.imports.get(name)
        if before is None or after is None:
            cell = after or before
            detail = f"{cell.cell} from {cell.file}"
            changes.append(
                Change(
                    ADDED if before is None else REMOVED, IMPORTS, "import", name, (detail,), name
                )
            )
            continue
        keys = ("file", "cell", "layers", "description")
        details = list(
            _fields({k: getattr(before, k) for k in keys}, {k: getattr(after, k) for k in keys})
        )
        if before.digest != after.digest:
            details.append("new version of the file")
        if details:
            changes.append(Change(CHANGED, IMPORTS, "import", name, tuple(details), name))
    return changes


# -- components ------------------------------------------------------------------


def _component(name: str, old: ComponentDef | None, new: ComponentDef | None) -> list[Change]:
    if old is None or new is None:
        action = ADDED if old is None else REMOVED
        return [Change(action, name, "component", "", component=name if new else None)]
    changes = []
    if old.description != new.description:
        changes.append(Change(CHANGED, name, "description", "", component=name))
    for what, before, after in (
        ("parameter", old.parameters, new.parameters),
        ("point", old.points, new.points),
    ):
        changes += _named(name, what, before, after)
    changes += _shapes(name, old.shapes, new.shapes, (), 0, (), None)
    return changes


def _named(component: str, what: str, old: list, new: list) -> list[Change]:
    """Parameters or points, matched by name."""
    before = {item.name: item for item in old}
    after = {item.name: item for item in new}
    changes = []
    for name in _union(before, after):
        if name not in before or name not in after:
            action = ADDED if name in after else REMOVED
            changes.append(Change(action, component, what, name, component=component))
            continue
        details = _fields(_dump(before[name]), _dump(after[name]))
        if details:
            changes.append(Change(CHANGED, component, what, name, details, component))
    return changes


def _shapes(
    component: str,
    old: list[Shape],
    new: list[Shape],
    prefix: NodePath,
    slot: int,
    labels: tuple[str, ...],
    parent: NodePath | None,
) -> list[Change]:
    """The changes in one list of shapes (and inside the shapes in both versions)."""
    pairs, added, removed = _match(old, new)
    events: list[tuple[int, list[Change]]] = []  # by position in the new list
    for index in added:
        shape = new[index]
        path = (*prefix, (slot, index))
        change = Change(ADDED, component, "shape", _label(labels, shape), (), component, path)
        events.append((index, [change]))
    for before, index in pairs:
        after = new[index]
        path = (*prefix, (slot, index))
        label = _label(labels, after)
        found = []
        details = _fields(_dump(before), _dump(after))
        if details:
            found.append(Change(CHANGED, component, "shape", label, details, component, path))
        inner = (*labels, after.name or _kind(after))
        for child_slot, (a, b) in enumerate(
            zip(before.child_lists(), after.child_lists(), strict=False)
        ):
            found += _shapes(component, a, b, path, child_slot, inner, path)
        events.append((index, found))
    changes = [c for _, found in sorted(events, key=lambda e: e[0]) for c in found]
    for shape in removed:
        changes.append(
            Change(REMOVED, component, "shape", _label(labels, shape), (), component, parent)
        )
    return changes


def _match(
    old: list[Shape], new: list[Shape]
) -> tuple[list[tuple[Shape, int]], list[int], list[Shape]]:
    """Pairs (old shape, index in ``new``), the indices of new shapes, and the
    old shapes that are gone. Named shapes are matched by name; the others by
    content (an unchanged shape keeps its match wherever it moved in the list),
    then in order among those of the same kind."""
    new_names = {s.name for s in new if s.name}
    old_by_name = {s.name: s for s in old if s.name and s.name in new_names}
    pairs = [(old_by_name[s.name], i) for i, s in enumerate(new) if s.name in old_by_name]
    old_rest = [s for s in old if not (s.name and s.name in old_by_name)]
    new_rest = [i for i, s in enumerate(new) if not (s.name and s.name in old_by_name)]
    added: list[int] = []
    removed: list[Shape] = []
    matcher = SequenceMatcher(
        None, [_json(s) for s in old_rest], [_json(new[i]) for i in new_rest], autojunk=False
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        before, after = old_rest[i1:i2], new_rest[j1:j2]
        if tag == "equal":
            pairs += list(zip(before, after, strict=True))
            continue
        for a, j in zip(before, after, strict=False):
            if type(a) is type(new[j]):
                pairs.append((a, j))
            else:
                removed.append(a)
                added.append(j)
        removed += before[len(after) :]
        added += after[len(before) :]
    return pairs, added, removed


def _label(parents: tuple[str, ...], shape: Shape) -> str:
    return " › ".join((*parents, shape.name or _kind(shape)))


def _kind(shape: Shape) -> str:
    return type(shape).kind_name()


def _dump(model) -> dict[str, Any]:
    """A model's fields as plain data, without the shapes inside it."""
    exclude = set(getattr(type(model), "child_fields", ()))
    return model.model_dump(mode="json", exclude=exclude)


def _json(shape: Shape) -> str:
    return json.dumps(shape.model_dump(mode="json"), sort_keys=True)


# -- words -------------------------------------------------------------------------


def _fields(old: dict[str, Any], new: dict[str, Any], prefix: str = "") -> tuple[str, ...]:
    """``field old → new`` for every field that differs; inside a plain mapping (a
    placement's ``params``, an ``align``) per entry: ``params.fingers 16 → 10``."""
    details: list[str] = []
    for key in _union(old, new):
        before, after = old.get(key), new.get(key)
        if before == after:
            continue
        if _mapping(before) and _mapping(after):
            details += _fields(before, after, f"{prefix}{key}.")
        elif prefix:
            details.append(f"{prefix}{key} {_short(before)} → {_short(after)}")
        elif key == "enabled":
            details.append("switched on" if after else "switched off")
        elif key == "description":
            details.append("description changed")
        else:
            details.append(f"{key} {_short(before)} → {_short(after)}")
    return tuple(details)


def _mapping(value: Any) -> bool:
    """A dict of values, not a model with a kind (a modifier reads better whole)."""
    return isinstance(value, dict) and "kind" not in value


def _short(value: Any) -> str:
    text = _words(value)
    return text if len(text) <= MAX_VALUE_CHARS else text[: MAX_VALUE_CHARS - 1] + "…"


def _words(value: Any) -> str:
    if value is None or value == [] or value == {}:
        return "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, list | tuple):
        if all(isinstance(v, int | float) for v in value):
            return f"({', '.join(_words(v) for v in value)})"
        return ", ".join(_words(v) for v in value)
    if isinstance(value, dict):
        kind = value.get("kind")
        rest = ", ".join(
            f"{k} {_words(v)}" for k, v in value.items() if k != "kind" and v not in (None, [], {})
        )
        return f"{kind} ({rest})" if kind else rest
    return str(value)


def _union(first: Iterable[str], second: Iterable[str]) -> list[str]:
    """The keys of both, in the order of the second, then those only in the first."""
    return list(dict.fromkeys([*second, *first]))
