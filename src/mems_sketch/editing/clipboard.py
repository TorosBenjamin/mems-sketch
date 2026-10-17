"""Copying, cutting and pasting shapes, within a component or into another one.

What is copied is plain YAML text (so it can go through the system clipboard,
also between windows): the shapes, the parameters of their component they use
and, for the components they place, unique names, so that they find the same
components from wherever they are pasted.
"""

from __future__ import annotations

from typing import Any

from mems_sketch.core.project import Project, check_shape_names
from mems_sketch.core.shapes import SHAPE_ADAPTER, NodePath, RefShape, Shape, node_at, walk
from mems_sketch.core.shapes.rewrite import rename_node_references
from mems_sketch.core.user_component import ParamDef
from mems_sketch.editing.commands import Commands
from mems_sketch.editing.components import parameters_used
from mems_sketch.editing.naming import fresh_name
from mems_sketch.storage import yaml_format

FORMAT = "mems-sketch/shapes"  # what marks the text as shapes


class Pasted(list):
    """The paths of the pasted shapes, and what was noticed on the way (``notes``:
    parameters that already existed with another default)."""

    def __init__(self, paths: list[NodePath], notes: list[str]) -> None:
        super().__init__(paths)
        self.notes = notes


class ClipboardEdits(Commands):
    """Copy, cut and paste of the active component's shapes."""

    def copy(self, paths: list[NodePath]) -> str:
        """The shapes at ``paths`` as clipboard text (a node inside a selected one
        goes with it, not again)."""
        session = self.session
        shapes = [node_at(session.shapes, p) for p in _outermost(paths)]
        if not shapes:
            raise ValueError("select shapes to copy")
        project, active = session.project, session.active
        copies = [s.model_copy(deep=True) for s in shapes]
        for node in walk(copies):
            if isinstance(node, RefShape):  # written so that any component finds it
                node.component = project.qualify(node.component, active)
        data: dict[str, Any] = {"format": FORMAT, "from": active}
        parameters = parameters_used(session.active_definition, shapes)
        if parameters:
            data["parameters"] = [yaml_format.to_data(p) for p in parameters]
        data["shapes"] = [yaml_format.to_data(s) for s in copies]
        return yaml_format.dump(data)

    def cut(self, paths: list[NodePath]) -> str:
        """Copy the shapes and delete them (one step to undo)."""
        text = self.copy(paths)
        self.session.nodes.remove(_outermost(paths))
        return text

    def paste(self, text: str) -> Pasted:
        """Add the shapes in clipboard ``text`` to the active component, with fresh
        names where theirs are taken; the parameters they use are added where
        missing. One step to undo. Refused when it is not shapes, or when they
        place a component the active one cannot place (another's private one)."""
        shapes, parameters = _read(text)
        session = self.session
        project, active = session.project, session.active
        for node in walk(shapes):
            if isinstance(node, RefShape):
                node.component = _written(project, node.component, active)
        taken = {s.name for s in walk(session.shapes) if s.name}
        for name in [s.name for s in walk(shapes) if s.name]:  # pasted twice: fresh names
            if name in taken:
                new = fresh_name(name, taken | {s.name for s in walk(shapes) if s.name})
                shapes = rename_node_references(shapes, name, new)
                next(s for s in walk(shapes) if s.name == name).name = new
                name = new
            taken.add(name)
        existing = {p.name: p for p in session.active_definition.parameters}
        missing = [p for p in parameters if p.name not in existing]
        notes = [
            f"'{p.name}' is {existing[p.name].default} here, {p.default} where it was copied "
            "from: the pasted shapes use this one"
            for p in parameters
            if p.name in existing and existing[p.name].default != p.default
        ]
        start = len(session.shapes)

        def change(p: Project) -> None:
            definition = session.local(p)
            definition.parameters.extend(missing)
            definition.shapes.extend(shapes)
            check_shape_names(definition.shapes)

        count = len(shapes)
        session.edit("Paste" if count != 1 else f"Paste {shapes[0].name}", change)
        return Pasted([((0, start + k),) for k in range(count)], notes)


def is_shapes(text: str) -> bool:
    """Whether clipboard text holds shapes (to enable Paste)."""
    if FORMAT not in text:
        return False
    try:
        data = yaml_format.load(text)
    except Exception:  # noqa: BLE001 - any text may be on the clipboard
        return False
    return isinstance(data, dict) and data.get("format") == FORMAT


def _read(text: str) -> tuple[list[Shape], list[ParamDef]]:
    if not is_shapes(text):
        raise ValueError("the clipboard holds no shapes")
    data = yaml_format.load(text)
    shapes = [SHAPE_ADAPTER.validate_python(s) for s in data.get("shapes") or []]
    parameters = [ParamDef.model_validate(p) for p in data.get("parameters") or []]
    return shapes, parameters


def _written(project: Project, qualified: str, active: str) -> str:
    """How ``active`` writes a reference to ``qualified``; refused when it cannot."""
    try:
        project.qualify(qualified)
    except KeyError:
        raise ValueError(f"there is no component '{qualified}' in this project") from None
    written = project.reference_name(qualified, active)
    try:
        if project.qualify(written, active) == qualified:
            return written
    except KeyError:
        pass
    owner = qualified.rpartition("/")[0] or "another component"
    raise ValueError(
        f"'{qualified}' is private to {owner}: it cannot be placed in {active}; "
        "make it shared first"
    )


def _outermost(paths: list[NodePath]) -> list[NodePath]:
    """The paths that are not inside another of them, in order."""
    unique = sorted(set(paths))
    return [p for p in unique if not any(p[: len(q)] == q and p != q for q in unique)]
