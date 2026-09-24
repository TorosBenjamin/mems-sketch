"""Editing a node's modifier stack: add, change, reorder, switch off, remove, apply.

See :mod:`mems_sketch.core.shapes.modifiers` for what the modifiers do.
Modifiers are addressed by the node's path and their index in its stack.
"""

from __future__ import annotations

from typing import Any

from mems_sketch.core.project import Project, check_shape_names
from mems_sketch.core.shapes import (
    NodePath,
    Shape,
    TransformShape,
    container_of,
    rename_node_references,
    walk,
)
from mems_sketch.core.shapes.modifiers import (
    MODIFIER_ADAPTER,
    Modifier,
    new_modifier,
)
from mems_sketch.core.shapes.points import NodePoints
from mems_sketch.editing.commands import Commands
from mems_sketch.editing.naming import fresh_name


class ModifierEdits(Commands):
    """A node's modifier stack; every command is one undo step."""

    def add(self, path: NodePath, kind: str, **fields: Any) -> int:
        """Add a modifier at the end of the stack; returns its index."""
        modifier = new_modifier(kind)
        if fields:
            modifier = _validated(modifier, fields)
        node = self.session.node(path)
        self._set(path, [*node.modifiers, modifier], f"Add {kind} to {_label(node)}")
        return len(node.modifiers)

    def update(self, path: NodePath, index: int, /, **fields: Any) -> None:
        node = self.session.node(path)
        modifiers = list(node.modifiers)
        modifiers[index] = _validated(modifiers[index], fields)
        self._set(path, modifiers, f"Change {modifiers[index].kind} of {_label(node)}")

    def set_enabled(self, path: NodePath, index: int, enabled: bool) -> None:
        self.update(path, index, enabled=enabled)

    def remove(self, path: NodePath, index: int) -> None:
        node = self.session.node(path)
        modifiers = list(node.modifiers)
        removed = modifiers.pop(index)
        self._set(path, modifiers, f"Remove {removed.kind} from {_label(node)}")

    def move(self, path: NodePath, index: int, to: int) -> None:
        """Move a modifier to another place in the stack (the order matters)."""
        node = self.session.node(path)
        modifiers = list(node.modifiers)
        if not 0 <= to < len(modifiers):
            raise ValueError(f"no place {to} in a stack of {len(modifiers)}")
        modifiers.insert(to, modifiers.pop(index))
        self._set(path, modifiers, f"Reorder modifiers of {_label(node)}")

    def apply(self, path: NodePath) -> NodePath:
        """Turn the first modifier into real shapes, like Blender's Apply.

        The node becomes a transform (with the node's name, alignment and
        remaining modifiers) holding one copy per result of the modifier, with
        array indices filled in. The copies can then be edited one by one;
        they are no longer parametric in the modifier's settings. A copy of a
        node with named parts gets fresh names for them.
        """
        node = self.session.node(path)
        if not node.modifiers:
            raise ValueError(f"'{_label(node)}' has no modifiers to apply")
        first, rest = node.modifiers[0], node.modifiers[1:]
        variables = {**self.session.results.scope(path), "i": 0.0, "j": 0.0}
        plain = node.model_copy(update={"modifiers": [], "align": None, "name": None})
        if not first.enabled:
            result = node.model_copy(update={"modifiers": rest})
        else:
            copies = self._copies(first, plain, variables, path)
            result = TransformShape(
                name=node.name,
                children=copies,
                modifiers=rest,
                align=node.align,
                enabled=node.enabled,
            )

        def change(project: Project) -> None:
            container, index = container_of(self.session.shapes_in(project), path)
            container[index] = result
            check_shape_names(self.session.shapes_in(project))

        self.session.edit(f"Apply {first.kind} of {_label(node)}", change)
        return path

    # -- helpers -----------------------------------------------------------------

    def _set(self, path: NodePath, modifiers: list[Modifier], description: str) -> None:
        def change(project: Project) -> None:
            container, index = container_of(self.session.shapes_in(project), path)
            container[index] = container[index].model_copy(update={"modifiers": modifiers})

        self.session.edit(description, change)

    def _copies(
        self, modifier: Modifier, node: Shape, variables: dict[str, float], path: NodePath
    ) -> list[Shape]:
        """The shapes one modifier makes of ``node``, with fresh names for repeated parts."""
        copies = modifier.baked(node, variables, lambda shape: self._measure(shape, variables))
        return _fresh_names(copies, {s.name for s in walk(self.session.shapes) if s.name})

    def _measure(self, node: Shape, variables: dict[str, float]) -> NodePoints:
        """The points of ``node`` (without its modifiers) where it is."""
        geometry = self.session.project.render_shape(node, self.session.active, variables)
        return NodePoints(node.name or node.kind, geometry, {})


def _validated(modifier: Modifier, fields: dict[str, Any]) -> Modifier:
    return MODIFIER_ADAPTER.validate_python({**modifier.model_dump(), **fields})


def _label(node: Shape) -> str:
    return node.name or node.kind


def _fresh_names(copies: list[Shape], taken: set[str]) -> list[Shape]:
    """Copies after the first get fresh names for their named parts."""
    result = []
    for number, copy in enumerate(copies):
        if number:
            for old in [s.name for s in walk([copy]) if s.name]:
                new = fresh_name(old, taken)
                copy = rename_node_references([copy], old, new)[0]
                next(s for s in walk([copy]) if s.name == old).name = new
                taken.add(new)
        else:
            taken.update(s.name for s in walk([copy]) if s.name)
        result.append(copy)
    return result
