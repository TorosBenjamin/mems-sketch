"""Adding, replacing, removing, wrapping and aligning shapes of the active component."""

from __future__ import annotations

import math

from mems_sketch.core.project import Project, check_shape_name, check_shape_names
from mems_sketch.core.shapes import (
    Align,
    ConnectionShape,
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
            shift = record.shift  # bake the alignment's move into the coordinates
            node = translated(node.model_copy(update={"align": None}), shift.dx, shift.dy)
            self.replace(path, node)
            return
        self.replace(path, node.model_copy(update={"align": align}))

    def add(self, shape: Shape) -> NodePath:
        if shape.name is None:
            shape = shape.model_copy(update={"name": self.session.unique_name(shape.kind)})
        for node in walk([shape]):
            if node.name:
                check_shape_name(node.name)

        def change(project: Project) -> None:
            shapes = self.session.shapes_in(project)
            shapes.append(shape)
            check_shape_names(shapes)

        self.session.edit(f"Add {shape.name}", change)
        return ((0, len(self.session.shapes) - 1),)

    def add_primitive(self, kind: str, layer: str = "device") -> NodePath:
        return self.add(default_shape(kind, layer))

    def add_component(self, component: str, x: float = 0.0, y: float = 0.0) -> NodePath:
        """Place a component (its origin at ``x, y``) in the active component.

        ``component`` is its unique name (``comb/finger``, ``lib.plate``) or a
        name as the active component writes it; a private component can only be
        placed inside its owner.
        """
        project, active = self.session.project, self.session.active
        try:
            target = project.qualify(component, active)
        except KeyError:
            target = project.qualify(component)  # a unique name, e.g. from the explorer
        written = project.reference_name(target, active)
        try:
            project.qualify(written, active)  # a private component elsewhere is refused
        except KeyError as exc:
            raise ValueError(str(exc)) from None
        stem = written.rsplit(".", 1)[-1].rsplit("/", 1)[-1].split("_")[0]
        return self.add(RefShape(name=self.session.unique_name(stem), component=written, x=x, y=y))

    def replace(self, path: NodePath, new: Shape) -> None:
        """Replace a node. A new name is also used by alignments and point expressions."""
        old_name = self.session.node(path).name
        had = {n.name for n in walk([self.session.node(path)]) if n.name}
        for node in walk([new]):
            if node.name and node.name not in had:
                check_shape_name(node.name)

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

    def connect(self, paths: list[NodePath], **fields) -> NodePath:
        """A connection joining sibling shapes' ends, placed after the last of them.

        For each shape the end is its port facing the others: of those pointing
        towards the centre of all their ports, the nearest to it (an array's copies
        by index are left out: name one in the connection's ends instead).
        """
        if len(paths) < 2:
            raise ValueError("select two or more shapes with ports to connect")
        ordered, _, nodes = self.siblings(paths)
        results = self.session.results
        ports = []
        for path, node in zip(ordered, nodes, strict=True):
            own = {n: p for n, p in results.node_ports(path).items() if "[" not in n}
            if not own:
                raise ValueError(f"{node.name or node.kind} has no ports to connect")
            ports.append(own)
        every = [p for own in ports for p in own.values()]
        cx = sum(p.x for p in every) / len(every)
        cy = sum(p.y for p in every) / len(every)

        def facing(port) -> bool:
            a = math.radians(port.angle)
            return math.cos(a) * (cx - port.x) + math.sin(a) * (cy - port.y) > 0

        ends = []
        for node, own in zip(nodes, ports, strict=True):
            towards = {n: p for n, p in own.items() if facing(p)}
            if not towards:
                raise ValueError(f"no port of {node.name} faces the others")
            name = min(towards, key=lambda n: math.hypot(towards[n].x - cx, towards[n].y - cy))
            ends.append(f"{node.name}.port.{name}")
        shape = ConnectionShape(name=self.session.unique_name("connection"), ends=ends, **fields)
        last = ordered[-1]

        def change(project: Project) -> None:
            target, index = container_of(self.session.shapes_in(project), last)
            target.insert(index + 1, shape)

        self.session.edit(f"Connect {len(ends)} ends", change)
        return (*last[:-1], (last[-1][0], last[-1][1] + 1))

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
