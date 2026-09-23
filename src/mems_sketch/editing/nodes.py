"""Adding, replacing, removing, wrapping and aligning shapes of the active component."""

from __future__ import annotations

from mems_sketch.core.project import Project, check_shape_names
from mems_sketch.core.shapes import (
    Align,
    NodePath,
    RefShape,
    Shape,
    child_lists,
    container_of,
    default_shape,
    translated,
    walk,
    wrap_shapes,
)
from mems_sketch.editing.commands import Commands
from mems_sketch.editing.naming import fresh_name


class NodeEdits(Commands):
    """Adding, replacing, removing, wrapping and aligning shapes of the active component."""

    def set_align(self, path: NodePath, align: Align | None) -> None:
        """Align a node. ``None`` removes its alignment and leaves it where it is."""
        node = self.session.node(path)
        record = self.session.results.inspection().get(path)
        if align is None and node.align is not None and record is not None:
            shift = record.shift.disp  # bake the alignment's move into the coordinates
            node = translated(node.model_copy(update={"align": None}), shift.x, shift.y)
            self.replace(path, node)
            return
        self.replace(path, node.model_copy(update={"align": align}))

    def add(self, shape: Shape) -> NodePath:
        if shape.name is None:
            shape = shape.model_copy(update={"name": self.session.unique_name(shape.kind)})
        self.session.edit(f"Add {shape.name}", lambda p: self.session.shapes_in(p).append(shape))
        return ((0, len(self.session.shapes) - 1),)

    def add_primitive(self, kind: str, layer: str = "device") -> NodePath:
        return self.add(default_shape(kind, layer))

    def add_component(self, component: str, x: float = 0.0, y: float = 0.0) -> NodePath:
        """Place a component (its origin at ``x, y``) in the active component."""
        stem = component.rsplit(".", 1)[-1].split("_")[0]
        return self.add(
            RefShape(name=self.session.unique_name(stem), component=component, x=x, y=y)
        )

    def replace(self, path: NodePath, new: Shape) -> None:
        """Replace a node. A new name is also used by alignments and point expressions."""
        old_name = self.session.node(path).name

        def change(project: Project) -> None:
            if old_name and new.name and new.name != old_name:
                self.session.local(project).rename_shape(old_name, new.name)
            container, index = container_of(self.session.shapes_in(project), path)
            container[index] = new
            check_shape_names(self.session.shapes_in(project))

        self.session.edit(f"Edit {new.name or new.kind}", change)

    def remove(self, paths: list[NodePath]) -> None:
        def change(project: Project) -> None:
            # Resolve all targets before removing anything, so indices stay valid.
            targets = [container_of(self.session.shapes_in(project), p) for p in paths]
            doomed = {(id(c), i) for c, i in targets}
            for container in {id(c): c for c, _ in targets}.values():
                container[:] = [
                    s for i, s in enumerate(container) if (id(container), i) not in doomed
                ]

        self.session.edit("Delete", change)

    def duplicate(self, path: NodePath) -> NodePath:
        copy_ = self.session.node(path).model_copy(deep=True)
        taken = {s.name for s in walk(self.session.shapes) if s.name}
        for node in walk([copy_]):
            if node.name:
                node.name = fresh_name(node.name, taken)
                taken.add(node.name)
        return self.add(copy_)

    def set_enabled(self, path: NodePath, enabled: bool) -> None:
        node = self.session.node(path)
        self.replace(path, node.model_copy(update={"enabled": enabled}))

    def siblings(self, paths: list[NodePath]) -> tuple[list[NodePath], int, list[Shape]]:
        if not paths:
            raise ValueError("select one or more shapes first")
        if len({p[:-1] + ((p[-1][0], -1),) for p in paths}) != 1:
            raise ValueError("the selected shapes must be siblings (same parent)")
        ordered = sorted(paths, key=lambda p: p[-1][1])
        container, _ = container_of(self.session.shapes, ordered[0])
        return ordered, ordered[0][-1][1], [container[p[-1][1]] for p in ordered]

    def wrap(self, paths: list[NodePath], operation: str) -> NodePath:
        """Replace sibling nodes with a new operation node that contains them.

        ``operation`` is ``transform``, ``offset``, ``fillet``, ``layer_map`` or one of
        the boolean ops; for booleans the first node becomes ``a`` and the rest ``b``.
        """
        ordered, first, nodes = self.siblings(paths)
        name = self.session.unique_name("transform" if operation == "group" else operation)
        wrapper = wrap_shapes(operation, name, nodes)
        indices = {p[-1][1] for p in ordered}

        def change(project: Project) -> None:
            target, _ = container_of(self.session.shapes_in(project), ordered[0])
            kept = [s for i, s in enumerate(target) if i not in indices]
            target[:] = kept[:first] + [wrapper] + kept[first:]

        self.session.edit(
            f"{operation.replace('_', ' ').capitalize()} {len(nodes)} shape(s)", change
        )
        return (*ordered[0][:-1], (ordered[0][-1][0], first))

    def unwrap(self, path: NodePath) -> None:
        """Replace an operation node by its children (the inverse of :meth:`wrap`)."""
        node = self.session.node(path)
        children = [c for group in child_lists(node) for c in group]
        if not children:
            raise ValueError("only operations can be unwrapped")

        def change(project: Project) -> None:
            container, index = container_of(self.session.shapes_in(project), path)
            container[index : index + 1] = children

        self.session.edit(f"Unwrap {node.name or node.kind}", change)
