"""The open design, as the GUI sees it: edits, undo/redo, files and selection-based operations.

Every edit runs as a transaction: the change is applied to the design, the
design is re-rendered, and if anything fails the design is restored exactly.
Undo and redo work on whole-design snapshots, which is simple and robust for
the model sizes involved.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from mems_sketch.core.component import Geometry, component_types
from mems_sketch.core.design import Design, Layer
from mems_sketch.core.shapes import (
    ArcShape,
    BooleanShape,
    CircleShape,
    FilletShape,
    GroupShape,
    LayerMapShape,
    NodePath,
    OffsetShape,
    PathShape,
    PolygonShape,
    RectShape,
    RefShape,
    Shape,
    child_lists,
    container_of,
    node_at,
    walk,
)
from mems_sketch.export.base import export
from mems_sketch.process import etch, rules
from mems_sketch.storage.sqlite_store import load, save

UNDO_LIMIT = 200
VIEW_MODES = {"drawn": "Drawn", "etched": "As etched", "compensated": "Etch compensated"}


def new_design() -> Design:
    design = Design(name="untitled")
    design.add_layer(Layer("device", 1, 0, undercut=0.0, min_width=2.0, min_space=2.0))
    design.add_layer(Layer("anchor", 2, 0))
    design.add_layer(Layer("metal", 3, 0))
    return design


class DesignDocument(QObject):
    changed = Signal()  # the model changed; views must refresh
    file_changed = Signal()  # path or dirty flag changed

    def __init__(self, design: Design | None = None) -> None:
        super().__init__()
        self.design = design or new_design()
        self.path: Path | None = None
        self.dirty = False
        self._undo: list[tuple[str, Design]] = []
        self._redo: list[tuple[str, Design]] = []

    # -- transactions ------------------------------------------------------

    def edit(self, description: str, change: Callable[[Design], object]) -> object:
        """Apply ``change`` to the design; roll back and re-raise if the result is invalid."""
        before = copy.deepcopy(self.design)
        was_valid = self._renders(before)
        try:
            result = change(self.design)
            if was_valid:
                self.design.render()
        except Exception:
            self.design = before
            raise
        self._undo.append((description, before))
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()
        self._set_dirty(True)
        self.changed.emit()
        return result

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_text(self) -> str:
        return self._undo[-1][0] if self._undo else ""

    def redo_text(self) -> str:
        return self._redo[-1][0] if self._redo else ""

    def undo(self) -> None:
        if self._undo:
            description, state = self._undo.pop()
            self._redo.append((description, self.design))
            self.design = state
            self._set_dirty(True)
            self.changed.emit()

    def redo(self) -> None:
        if self._redo:
            description, state = self._redo.pop()
            self._undo.append((description, self.design))
            self.design = state
            self._set_dirty(True)
            self.changed.emit()

    # -- files -------------------------------------------------------------

    def new(self) -> None:
        self._reset(new_design(), None)

    def open(self, path: str | Path) -> None:
        self._reset(load(path), Path(path))

    def save(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path is not None else self.path
        if target is None:
            raise ValueError("no file name given")
        save(self.design, target)
        self.path = target
        self._set_dirty(False)
        return target

    def export(self, path: str | Path, mode: str = "drawn") -> Path:
        return export(self.design, path, geometry=self.geometry(mode))

    def _reset(self, design: Design, path: Path | None) -> None:
        self.design = design
        self.path = path
        self._undo.clear()
        self._redo.clear()
        self._set_dirty(False)
        self.changed.emit()

    def _set_dirty(self, dirty: bool) -> None:
        self.dirty = dirty
        self.file_changed.emit()

    # -- queries -----------------------------------------------------------

    def geometry(self, mode: str = "drawn") -> Geometry:
        drawn = self.design.render()
        if mode == "etched":
            return etch.etched(self.design, drawn)
        if mode == "compensated":
            return etch.compensated(self.design, drawn)
        return drawn

    def check(self) -> list[rules.Violation]:
        return rules.check(self.design)

    def component_names(self) -> list[str]:
        return [*component_types(), *self.design.components]

    def node(self, path: NodePath) -> Shape:
        return node_at(self.design.shapes, path)

    def unique_name(self, stem: str) -> str:
        return _fresh(stem, {s.name for s in walk(self.design.shapes) if s.name})

    # -- shape edits -------------------------------------------------------

    def add_shape(self, shape: Shape) -> NodePath:
        if shape.name is None:
            shape = shape.model_copy(update={"name": self.unique_name(shape.kind)})
        self.edit(f"Add {shape.name}", lambda d: d.shapes.append(shape))
        return ((0, len(self.design.shapes) - 1),)

    def add_primitive(self, kind: str, layer: str = "device") -> NodePath:
        return self.add_shape(default_primitive(kind, layer))

    def add_component(self, component: str) -> NodePath:
        name = self.unique_name(component.split("_")[0])
        return self.add_shape(RefShape(name=name, component=component))

    def replace_node(self, path: NodePath, new: Shape) -> None:
        def change(design: Design) -> None:
            container, index = container_of(design.shapes, path)
            container[index] = new
            Design._check_names(design.shapes)

        self.edit(f"Edit {new.name or new.kind}", change)

    def remove_nodes(self, paths: list[NodePath]) -> None:
        def change(design: Design) -> None:
            # Resolve all targets before removing anything, so indices stay valid.
            targets = [(container_of(design.shapes, p)) for p in paths]
            doomed = {(id(c), i) for c, i in targets}
            for container in {id(c): c for c, _ in targets}.values():
                container[:] = [
                    s for i, s in enumerate(container) if (id(container), i) not in doomed
                ]

        self.edit("Delete", change)

    def duplicate(self, path: NodePath) -> NodePath:
        original = self.node(path)
        copy_ = original.model_copy(deep=True)
        taken_before = {s.name for s in walk(self.design.shapes) if s.name}
        for node in walk([copy_]):
            if node.name:
                node.name = _fresh(node.name, taken_before)
                taken_before.add(node.name)
        return self.add_shape(copy_)

    def set_enabled(self, path: NodePath, enabled: bool) -> None:
        node = self.node(path)
        self.replace_node(path, node.model_copy(update={"enabled": enabled}))

    def wrap(self, paths: list[NodePath], operation: str) -> NodePath:
        """Replace sibling nodes with a new operation node that contains them.

        ``operation`` is ``group``, ``offset``, ``fillet``, ``layer_map`` or one of
        the boolean ops; for booleans the first node becomes ``a`` and the rest ``b``.
        """
        if not paths:
            raise ValueError("select one or more shapes first")
        parents = {p[:-1] + ((p[-1][0], -1),) for p in paths}
        if len(parents) != 1:
            raise ValueError("the selected shapes must be siblings (same parent)")
        ordered = sorted(paths, key=lambda p: p[-1][1])
        container, _ = container_of(self.design.shapes, ordered[0])
        nodes = [container[p[-1][1]] for p in ordered]
        name = self.unique_name(operation)
        match operation:
            case "union" | "subtract" | "intersect" | "xor":
                if len(nodes) < 2:
                    raise ValueError(f"{operation} needs at least two selected shapes")
                wrapper: Shape = BooleanShape(name=name, op=operation, a=nodes[:1], b=nodes[1:])
            case "group":
                wrapper = GroupShape(name=name, children=nodes)
            case "offset":
                wrapper = OffsetShape(name=name, distance=1.0, children=nodes)
            case "fillet":
                wrapper = FilletShape(name=name, radius=1.0, children=nodes)
            case "layer_map":
                layers = sorted({getattr(n, "layer", "device") for n in walk(nodes)} - {None})
                wrapper = LayerMapShape(
                    name=name,
                    mapping={layer: layer for layer in layers} or {"device": "device"},
                    children=nodes,
                )
            case _:
                raise ValueError(f"unknown operation '{operation}'")
        first = ordered[0][-1][1]
        indices = {p[-1][1] for p in ordered}

        def change(design: Design) -> None:
            target, _ = container_of(design.shapes, ordered[0])
            kept = [s for i, s in enumerate(target) if i not in indices]
            target[:] = kept[:first] + [wrapper] + kept[first:]

        self.edit(f"{operation.replace('_', ' ').capitalize()} {len(nodes)} shape(s)", change)
        return (*ordered[0][:-1], (ordered[0][-1][0], first))

    def unwrap(self, path: NodePath) -> None:
        """Replace an operation node by its children (the inverse of :meth:`wrap`)."""
        node = self.node(path)
        children = [c for group in child_lists(node) for c in group]
        if not children:
            raise ValueError("only operations can be unwrapped")

        def change(design: Design) -> None:
            container, index = container_of(design.shapes, path)
            container[index : index + 1] = children

        self.edit(f"Unwrap {node.name or node.kind}", change)

    # -- variables and layers ----------------------------------------------

    def set_variable(self, name: str, value: float | str) -> None:
        self.edit(f"Set {name}", lambda d: d.set_variable(name, value))

    def rename_variable(self, old: str, new: str) -> None:
        def change(design: Design) -> None:
            if not new.isidentifier():
                raise ValueError(f"'{new}' is not a valid variable name")
            if new in design.variables:
                raise ValueError(f"variable '{new}' already exists")
            design.variables = {(new if k == old else k): v for k, v in design.variables.items()}

        self.edit(f"Rename {old}", change)

    def remove_variable(self, name: str) -> None:
        self.edit(f"Delete {name}", lambda d: d.variables.pop(name))

    def set_layer(self, name: str, layer: Layer) -> None:
        def change(design: Design) -> None:
            if layer.name != name and layer.name in design.layers:
                raise ValueError(f"layer '{layer.name}' already exists")
            design.layers = {
                (layer.name if k == name else k): (layer if k == name else v)
                for k, v in design.layers.items()
            }

        self.edit(f"Edit layer {name}", change)

    def add_layer(self) -> str:
        existing = self.design.layers
        n = 1
        while f"layer{n}" in existing:
            n += 1
        gds = max((ly.gds_layer for ly in existing.values()), default=0) + 1
        name = f"layer{n}"
        self.edit(f"Add layer {name}", lambda d: d.add_layer(Layer(name, gds)))
        return name

    def remove_layer(self, name: str) -> None:
        self.edit(f"Delete layer {name}", lambda d: d.layers.pop(name))

    def _renders(self, design: Design) -> bool:
        try:
            design.render()
            return True
        except Exception:
            return False


def _fresh(name: str, taken: set[str]) -> str:
    """``name`` with its numeric suffix replaced by the lowest one not in ``taken``."""
    stem = re.sub(r"\d+$", "", name) or "shape"
    n = 1
    while f"{stem}{n}" in taken:
        n += 1
    return f"{stem}{n}"


def default_primitive(kind: str, layer: str = "device") -> Shape:
    match kind:
        case "rect":
            return RectShape(layer=layer, x0=0, y0=0, x1=100, y1=50)
        case "circle":
            return CircleShape(layer=layer, radius=25)
        case "arc":
            return ArcShape(layer=layer, inner_radius=20, outer_radius=30, end_angle=180)
        case "polygon":
            return PolygonShape(layer=layer, points=[(0, 0), (60, 0), (30, 50)])
        case "path":
            return PathShape(layer=layer, points=[(0, 0), (100, 0), (100, 60)], width=4)
    raise ValueError(f"unknown primitive '{kind}'")
