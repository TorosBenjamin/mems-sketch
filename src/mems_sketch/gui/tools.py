"""Canvas tools: what a left click or drag on the canvas does.

Zooming (wheel) and panning (middle or right drag, or left drag while Space
is held) work in every tool; the canvas handles those itself. Everything else
goes to the active tool:

* **Select** (V): click to select, drag the selection to move it, drag on
  empty space to select with a box; drag a selected shape's handles (its
  sides, vertices, radii) to change it
* **Hand** (H): the left button pans
* **Move** (M): click a base point, then where it should go (both snap)
* **Rotate** (R): click a pivot, then the angle (snaps to 15°)
* **Align** (A): click a shape, one of its points, then the point to align to
* **Measure** (D): click two points; rulers stay until cleared
* **Measure angle** (N): click the vertex, then a point on each arm
* **Rectangle** (B) and **Circle** (C): drag, or click twice (corner and
  corner, centre and radius)
* **Polygon** (P) and **Path** (W): click the points; double-click, Enter or
  a click on the first point finishes, Backspace takes back the last point
* **Route** (E): click a port, any via points, then another port; drag a
  selected route's via points to move them

Drawing tools draw on the layer chosen in the toolbar, at the top level of
the component being edited. Their points snap to shape points, else to the
grid; Ctrl turns snapping off and Shift keeps squares and 45° angles.

A tool stays active until another one is chosen; Esc cancels what the tool
is doing, and pressed again goes back to Select. Tools that change the design
are refused on read-only tabs.

With a selection, Move shows a move gizmo (drag an arrow to move along x or
y, the centre circle to move freely) and Rotate a ring (drag it to rotate
about the selection's centre), as in Unity and Blender. Snapping distance, whether points and
the grid snap, and the rotation step come from the settings.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, ClassVar

import numpy as np
from PySide6.QtCore import Qt

from mems_sketch.core.expressions import evaluate
from mems_sketch.core.shapes import (
    Align,
    CircleShape,
    GuideShape,
    NodePath,
    PathShape,
    PolygonShape,
    RectShape,
    RouteShape,
    Shape,
)
from mems_sketch.core.shapes.handles import INSERT, VERTEX, entry_size, handle_label
from mems_sketch.engine import build_shapes
from mems_sketch.gui.canvas import angle_between

if TYPE_CHECKING:
    from mems_sketch.editing import DragPlan
    from mems_sketch.gui.app import MainWindow

SNAP_PX = 10  # default: a point snaps to another within this many pixels
DRAG_THRESHOLD_PX = 4  # a press moving less than this is a click, not a drag
GRID_POINTS = 64  # a move snaps the nearest of at most this many moving points to the grid
ANGLE_STEP = 15.0  # default: degrees the Rotate tool snaps to
EDIT_HANDLE_REACH_PX = 7  # a press this close to a shape's handle drags it
CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
ALT = Qt.KeyboardModifier.AltModifier
NONE = Qt.KeyboardModifier.NoModifier

Candidate = tuple[str, float, float]  # label, x, y


def grid_move(points, dx: float, dy: float, step: float, axes: str = "xy") -> tuple[float, float]:
    """``dx, dy`` changed so that one of the moving ``points`` lands on the grid.

    Of the points (``(x, y)`` before the move), the one that needs the least
    change is put on a grid line in each of ``axes``; the others keep their
    places relative to it. Without points, the move itself is whole grid steps.
    """
    if not points:
        return (
            round(dx / step) * step if "x" in axes else dx,
            round(dy / step) * step if "y" in axes else dy,
        )
    best = None
    for px, py in points[:GRID_POINTS]:
        mx = round((px + dx) / step) * step - px if "x" in axes else dx
        my = round((py + dy) / step) * step - py if "y" in axes else dy
        change = math.hypot(mx - dx, my - dy)
        if best is None or change < best[0]:
            best = (change, mx, my)
    return _um(best[1]), _um(best[2])


class Tool:
    """Base class: override the event methods a tool needs."""

    name: ClassVar[str]
    label: ClassVar[str]
    shortcut: ClassVar[str]
    edits: ClassVar[bool] = True  # changes the design, so refused on read-only tabs
    draws: ClassVar[bool] = False  # a drawing tool (started from Add, not a canvas mode)
    listed: ClassVar[bool] = True  # in the Tools menu (not a tool only started from elsewhere)
    gizmo: ClassVar[str | None] = None  # the gizmo shown on the selection, if any
    hovers: ClassVar[bool] = False  # outline the shape under the cursor
    icon: ClassVar[str] = "select"
    cursor: ClassVar[Qt.CursorShape] = Qt.CursorShape.ArrowCursor

    def __init__(self, window: MainWindow) -> None:
        self.window = window
        self._gizmo_drag = None

    @property
    def document(self):
        return self.window.document

    @property
    def canvas(self):
        return self.window.canvas

    # -- life cycle --------------------------------------------------------

    def activate(self) -> None:
        self.reset()
        self.window.prompt(self.hint())

    def deactivate(self) -> None:
        self.cancel()

    def reset(self) -> None:
        """Back to the first step, dropping anything in progress."""

    @property
    def busy(self) -> bool:
        return False

    def cancel(self) -> bool:
        """Stop what the tool is doing; True if it was doing something."""
        busy = self.busy
        self.reset()
        self.canvas.clear_drag_preview()
        self.canvas.show_box(None)
        self.canvas.set_cursor_label(None)
        return busy

    def design_changed(self) -> None:
        """After any edit or undo: by default, drop what the tool was doing."""
        self.cancel()

    def hint(self) -> str:
        return ""

    # -- events (x, y in µm, in the current tab's component) ----------------

    def press(self, x: float, y: float, modifiers) -> None:
        pass

    def move(self, x: float, y: float, modifiers, left: bool) -> None:
        pass

    def release(self, x: float, y: float, modifiers) -> None:
        pass

    def double_click(self, x: float, y: float) -> bool:
        """A double click; False lets the window use it (to open a placed component)."""
        return False

    def key(self, key: Qt.Key) -> bool:
        """Enter or Backspace on the canvas; True if the tool used it."""
        return False

    def typed(self, text: str) -> bool:
        """A digit, sign, point, comma or space typed on the canvas; True if the tool
        took it (e.g. as a value for a handle)."""
        return False

    def hover_label(self, x: float, y: float) -> str | None:
        """What the cursor is on, for the status bar (e.g. the point it would snap to)."""
        return None

    def markers(self) -> dict[str, list[Candidate]]:
        """Points to mark on the canvas, by style (see ``canvas.POINT_SIZES``)."""
        return {}

    # -- helpers -----------------------------------------------------------

    def editable(self) -> bool:
        if self.document.read_only:
            self.window.report_error(f"'{self.document.active}' is read-only")
            return False
        return True

    def setting(self, key: str, default):
        settings = getattr(self.window, "settings", None)
        return default if settings is None else settings.get(key)

    @property
    def reach(self) -> float:
        """The snapping distance in µm at the current zoom."""
        return self.setting("snapping/distance_px", SNAP_PX) / self.canvas.pixels_per_um()

    def snap(
        self, x: float, y: float, candidates: list[Candidate], modifiers, grid: bool = True
    ) -> tuple[float, float, str | None]:
        """``(x, y)`` snapped to the nearest candidate point in reach, else to the grid.

        Ctrl turns snapping off, and so do the settings for points or the grid.
        Returns the point and the candidate's label.
        """
        if modifiers & CTRL:
            return x, y, None
        best, found = self.reach, None
        if self.setting("snapping/points", True):
            for label, px, py in candidates:
                distance = math.hypot(px - x, py - y)
                if distance < best:
                    best, found = distance, (label, px, py)
        if found is not None:
            return found[1], found[2], found[0]
        if grid and self.setting("snapping/grid", True):
            step = self.canvas.grid_step()
            return round(x / step) * step, round(y / step) * step, None
        return x, y, None

    def snapped_move(self, plan, dx, dy, modifiers):
        """A move, snapped: a moving point onto another shape's point, else the grid.

        Returns ``dx, dy`` and, when a point snapped, ``(path, point, target, x, y)``.
        """
        if modifiers & CTRL:
            return dx, dy, None
        if self.setting("snapping/points", True):
            best, found = self.reach, None
            for path, name, px, py in plan.points:
                for target, tx, ty in plan.targets:
                    distance = math.hypot(px + dx - tx, py + dy - ty)
                    if distance < best:
                        best, found = distance, (path, name, target, tx, ty, px, py)
            if found is not None:
                path, name, target, tx, ty, px, py = found
                return tx - px, ty - py, (path, name, target, tx, ty)
        if not self.setting("snapping/grid", True):
            return dx, dy, None
        moving = [(px, py) for _, _, px, py in plan.points]
        return (*grid_move(moving, dx, dy, self.canvas.grid_step()), None)

    # -- the move gizmo (Select and Move) ---------------------------------

    def gizmo_press(self, x: float, y: float) -> bool:
        """Start dragging a part of the move gizmo under ``(x, y)``; False if none."""
        part = self.canvas.gizmo_hit(x, y) if self.canvas.gizmo else None
        if part not in ("x", "y", "free") or not self.window.selection:
            return False
        if not self.editable():
            return True
        plan = self.document.moves.plan_drag(self.window.selection)
        if not plan.roots:
            return True
        self._gizmo_drag = (part, (x, y), plan)
        self.canvas.set_gizmo_active(part)
        self.canvas.show_drag_preview(plan.preview, self.window.layers.colors)
        return True

    @property
    def gizmo_dragging(self) -> bool:
        return getattr(self, "_gizmo_drag", None) is not None

    def _gizmo_delta(self, x, y, modifiers):
        part, (x0, y0), plan = self._gizmo_drag
        dx, dy = x - x0, y - y0
        if part == "free":
            return self.snapped_move(plan, dx, dy, modifiers)[:2]
        along = dx if part == "x" else dy
        if not modifiers & CTRL and self.setting("snapping/grid", True):
            moving = [(px, py) for _, _, px, py in plan.points]
            gx, gy = grid_move(moving, dx, dy, self.canvas.grid_step(), axes=part)
            along = gx if part == "x" else gy
        return (along, 0.0) if part == "x" else (0.0, along)

    def gizmo_move(self, x, y, modifiers) -> None:
        dx, dy = self._gizmo_delta(x, y, modifiers)
        self.canvas.move_drag_preview(dx, dy)
        self.window.live(
            f"Move Δx {dx:g} µm, Δy {dy:g} µm   (Ctrl: no snapping)", _offset_label(dx, dy)
        )

    def gizmo_release(self, x, y, modifiers) -> None:
        dx, dy = self._gizmo_delta(x, y, modifiers)
        roots = self._gizmo_drag[2].roots
        self.gizmo_cancel()
        if dx or dy:
            self.window.run(lambda: self.document.moves.move(roots, dx, dy))
        self.window.prompt(self.hint())

    def gizmo_cancel(self) -> None:
        if self.gizmo_dragging:
            self._gizmo_drag = None
            self.canvas.clear_drag_preview()
            self.canvas.set_gizmo_active(None)


class SelectTool(Tool):
    """Also drags the selected shape's handles (its sides, vertices, radii; see
    :mod:`mems_sketch.editing.handles`): they snap as moving does, Shift keeps a
    vertex or corner on a straight line from where it was, Ctrl turns snapping off."""

    name, label, shortcut = "select", "Select", "V"
    edits = False
    hovers, icon = True, "select"
    shows_handles = True  # the window shows the selected shape's handles

    chosen: tuple[NodePath, str] | None = None  # a handle clicked: (shape path, key)

    def reset(self) -> None:
        self.entry: str | None = None  # a value being typed for a handle
        self._handle = None  # a HandleDrag in progress
        self._handle_to: tuple[float, float] | None = None
        self._handle_targets: list[Candidate] | None = None
        self._press: tuple[float, float] | None = None
        self._press_hit: NodePath | None = None
        self._pending: NodePath | None = None
        self._mode: str | None = None  # None, "move", "box" or "blocked"
        self._plan: DragPlan | None = None
        self._snap = None

    def hint(self) -> str:
        return (
            self._chosen_hint()
            or "Click to select, drag to move, drag on empty space to select with a box"
        )

    def press(self, x, y, modifiers) -> None:
        self.reset()
        if self.gizmo_press(x, y):
            return
        if self._press_handle(x, y):
            return
        self.chosen = None
        additive = bool(modifiers & (CTRL | SHIFT))
        hit = self.window.hit(x, y)
        self._press, self._press_hit = (x, y), hit
        if hit is not None and not additive and self.window.on_selection(x, y):
            self._pending = hit  # keep the selection: this may become a drag
            return
        self.window.select_click(hit, additive)

    def move(self, x, y, modifiers, left) -> None:
        if self.gizmo_dragging:
            if left:
                self.gizmo_move(x, y, modifiers)
            return
        if self._handle is not None:
            if left:
                self._drag_handle(x, y, modifiers)
            return
        if not left:
            self._hover_handle(x, y)
        if not left or self._press is None:
            return
        x0, y0 = self._press
        if self._mode is None:
            if math.hypot(x - x0, y - y0) * self.canvas.pixels_per_um() < DRAG_THRESHOLD_PX:
                return
            self._pending = None
            if self._press_hit is not None and self.window.selection:
                self._start_move()
            else:
                self._mode = "box"
        if self._mode == "move":
            self._drag_to(x - x0, y - y0, modifiers)
        elif self._mode == "box":
            self.canvas.show_box((x0, y0, x, y), self.window.box_touches(x0, x))

    def _start_move(self) -> None:
        if not self.editable():
            self._mode = "blocked"
            return
        self._plan = self.document.moves.plan_drag(self.window.selection)
        if not self._plan.roots:
            self._mode = "blocked"
            return
        self._mode = "move"
        self.canvas.show_drag_preview(self._plan.preview, self.window.layers.colors)

    _snapped_move = Tool.snapped_move

    def _drag_to(self, dx, dy, modifiers) -> None:
        dx, dy, self._snap = self.snapped_move(self._plan, dx, dy, modifiers)
        self.canvas.move_drag_preview(dx, dy)
        snap = self._snap
        self.canvas.show_points("snap", [(snap[2], snap[3], snap[4])] if snap else [])
        text = f"Move Δx {dx:g} µm, Δy {dy:g} µm"
        if snap:
            text += f"   {snap[1]} on {snap[2]} (release with Shift to align it there)"
        label = _offset_label(dx, dy, snap[2] if snap else None)
        offset = self._new_offset(dx, dy, modifiers)
        if offset is not None:  # an aligned shape: the drag changes its offset
            label = f"offset {offset[0]:g}, {offset[1]:g} µm"
        self.window.live(text, label)

    def _new_offset(self, dx, dy, modifiers) -> tuple[float, float] | None:
        """The offset a drag gives one aligned shape (None: not one aligned shape, a
        drag with Alt, which takes the alignment off, or an offset that is an
        expression)."""
        if modifiers & ALT or self._plan is None or len(self._plan.roots) != 1:
            return None
        align = self.document.node(self._plan.roots[0]).align
        if align is None or not all(isinstance(v, int | float) for v in (align.dx, align.dy)):
            return None
        return align.dx + dx, align.dy + dy

    def release(self, x, y, modifiers) -> None:
        if self.gizmo_dragging:
            self.gizmo_release(x, y, modifiers)
            return
        if self._handle is not None:
            self._release_handle(x, y, modifiers)
            return
        mode, press, pending, plan = self._mode, self._press, self._pending, self._plan
        self.cancel()
        self.canvas.show_points("snap", [])
        if mode == "move":
            dx, dy, snap = self.snapped_move(plan, x - press[0], y - press[1], modifiers)
            self._finish_move(plan, dx, dy, snap, modifiers)
        elif mode == "box":
            self.window.select_box(press[0], press[1], x, y, bool(modifiers & (CTRL | SHIFT)))
        elif pending is not None:
            self.window.select_click(pending, False)
        if mode != "blocked":  # keep the reason a drag was refused visible
            self.window.prompt(self.hint())

    def _finish_move(self, plan, dx, dy, snap, modifiers) -> None:
        window = self.window
        if modifiers & SHIFT and snap is not None and plan.roots == [snap[0]]:
            path, point, target = snap[0], snap[1], snap[2]
            ok, _ = window.run(
                lambda: self.document.nodes.set_align(path, Align(point=point, to=target))
            )
            if ok:
                window.notify(f"Aligned {point} to {target}", "success")
            return
        detach = bool(modifiers & ALT)
        window.run(lambda: self.document.moves.move(plan.roots, dx, dy, detach=detach))

    # -- the selected shape's handles ---------------------------------------

    def handle_at(self, x: float, y: float):
        """The handle shown under ``(x, y)`` µm (the nearest in reach), or None."""
        reach = (EDIT_HANDLE_REACH_PX) / self.canvas.pixels_per_um()
        best, found = reach, None
        for handle, hx, hy in self.window.edit_handles:
            distance = math.hypot(hx - x, hy - y)
            if distance <= best:
                best, found = distance, handle
        return found

    def _press_handle(self, x, y) -> bool:
        handle = self.handle_at(x, y)
        if handle is None or len(self.window.selection) != 1:
            return False
        if not self.editable():
            return True
        self._handle = self.document.handles.start(self.window.selection[0], handle.key)
        if self._handle is None:
            return False
        node = self._handle.node
        own = f"{node.name}." if node.name else None
        self._handle_targets = [
            c for c in self.document.results.all_points() if own is None or not c[0].startswith(own)
        ]
        self.window.update_overlay()
        return True

    def _handle_target(self, x, y, modifiers) -> tuple[float, float]:
        """Where the dragged handle goes for the cursor at ``(x, y)``: snapped, and with
        Shift kept level, upright or at 45° from where it started."""
        hx, hy = self._handle.at
        if modifiers & SHIFT:
            dx, dy = x - hx, y - hy
            angle = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
            ux, uy = round(math.cos(angle), 12), round(math.sin(angle), 12)
            length = dx * ux + dy * uy
            return _um(hx + length * ux), _um(hy + length * uy)
        px, py, label = self.snap(x, y, self._handle_targets or [], modifiers)
        self._snap = (label, px, py) if label else None
        return _um(px), _um(py)

    def _drag_handle(self, x, y, modifiers) -> None:
        self._snap = None
        to = self._handle_target(x, y, modifiers)
        self._handle_to = to
        edits = self.document.handles
        try:
            node = edits.dragged(self._handle, *to)
        except (ValueError, KeyError):  # e.g. a radius dragged through the centre
            return
        preview = edits.preview(self._handle, node)
        if preview is None:
            self.canvas.clear_drag_preview()
        else:
            self.canvas.show_drag_preview(preview, self.window.layers.colors)
        sketch = edits.sketch(self._handle, node)  # a guide, a route: nothing to build
        self.canvas.show_sketch(*(sketch or ([], False)))
        snap = self._snap
        self.canvas.show_points("snap", [snap] if snap else [])
        shown = self.canvas.edit_handles
        index = next(
            (i for i, (h, _, _) in enumerate(self.window.edit_handles) if h == self._handle.handle),
            None,
        )
        if index is not None:
            shown[index] = (*to, shown[index][2])
            self.canvas.show_edit_handles(shown, index)
        text = edits.describe(self._handle, node) or "no change"
        status = f"{text}   (Shift: straight, Ctrl: no snapping, Esc cancels)"
        if snap:
            status += f"   on {snap[0]}"
        self.window.live(status, text)

    def _release_handle(self, x, y, modifiers) -> None:
        drag, to = self._handle, self._handle_to
        self._stop()
        handle = drag.handle
        if to is None:  # a click: choose it (to delete, or type a value for it)
            self.chosen = None if handle.role == INSERT else (drag.path, handle.key)
        else:
            self.chosen = (drag.path, handle.key) if handle.role == VERTEX else None
            self.window.run(lambda: self.document.handles.drag(drag.path, handle.key, *to))
        self.window.update_overlay()
        self.window.prompt(self.hint())

    def _chosen_hint(self) -> str | None:
        handle = self._chosen_handle()
        if handle is not None:
            what = "x, y" if entry_size(handle) == 2 else "a value"
            delete = "Delete removes it, " if handle.role == VERTEX else ""
            return (
                f"{handle_label(handle.key).capitalize()} chosen: {delete}type {what} "
                "and Enter to set it, Esc lets go"
            )
        return None

    def _chosen_handle(self):
        """The chosen handle, while its shape is the one selected (else None)."""
        if self.chosen is None or self.window.selection != [self.chosen[0]]:
            return None
        return next((h for h, _, _ in self.window.edit_handles if h.key == self.chosen[1]), None)

    def active_handle(self) -> str | None:
        """The key of the handle drawn filled: the one dragged, else the chosen one."""
        if self._handle is not None:
            return self._handle.handle.key
        handle = self._chosen_handle()
        return handle.key if handle is not None else None

    # -- typing a value for a handle ------------------------------------------

    def _entry_handle(self):
        """The handle typed values go to: the one dragged, else the chosen one."""
        return self._handle.handle if self._handle is not None else self._chosen_handle()

    def typed(self, text: str) -> bool:
        handle = self._entry_handle()
        if handle is None or (self.entry is None and text.isspace()):
            return False  # (a space before anything typed pans, as always)
        self.entry = (self.entry or "") + text
        self._show_entry(handle)
        return True

    def _show_entry(self, handle) -> None:
        what = "x, y" if entry_size(handle) == 2 else "a value"
        label = f"{handle_label(handle.key)} = {self.entry}▏"
        self.window.live(
            f"Type {what} for {handle_label(handle.key)}: Enter sets it, Esc cancels", label
        )

    def key(self, key) -> bool:
        if self.entry is None:
            return False
        handle = self._entry_handle()
        if key == Qt.Key.Key_Backspace:
            self.entry = self.entry[:-1]
            if handle is not None:
                self._show_entry(handle)
            return True
        text = self.entry
        path = (
            self._handle.path
            if self._handle is not None
            else self.chosen[0]
            if self.chosen
            else None
        )
        self._stop()
        if handle is not None and path is not None and text.strip():
            self._type_in(path, handle, text)
        self.window.update_overlay()
        self.window.prompt(self.hint())
        return True

    def _type_in(self, path, handle, text: str) -> None:
        try:
            values = [float(part) for part in text.replace(",", " ").split()]
        except ValueError:
            self.window.report_error(f"'{text}' is not a number")
            return
        self.window.run(lambda: self.document.handles.type_in(path, handle.key, values))

    def delete_chosen(self) -> bool:
        """Delete with a vertex chosen: remove that vertex (True), not the shape."""
        if self.entry is not None:
            return True  # while a value is typed, Delete removes nothing
        handle = self._chosen_handle()
        if handle is None or handle.role != VERTEX or self.busy:
            return False
        path = self.chosen[0]
        self.chosen = None
        self.window.run(lambda: self.document.handles.remove(path, handle.key))
        self.window.update_overlay()
        self.window.prompt(self.hint())
        return True

    def _hover_handle(self, x, y) -> None:
        """The cursor over a handle shows which way it drags."""
        handle = self.handle_at(x, y) if self.window.edit_handles else None
        if handle is None:
            self.canvas.viewport().unsetCursor()
        else:
            self.canvas.viewport().setCursor(_handle_cursor(handle))

    @property
    def busy(self) -> bool:
        return (
            self._mode in ("move", "box")
            or self.gizmo_dragging
            or self._handle is not None
            or self.entry is not None
        )

    def cancel(self) -> bool:
        """Stop what it is doing; when idle, let go of the chosen handle."""
        busy = self._stop()
        if not busy:
            self.chosen = None
        return busy

    def design_changed(self) -> None:
        self._stop()  # (the chosen handle stays: e.g. after typing a value for it)

    def _stop(self) -> bool:
        busy = self.busy
        dragging_handle = self._handle is not None
        self.gizmo_cancel()
        Tool.cancel(self)
        self.canvas.show_points("snap", [])
        if dragging_handle:
            self.canvas.show_sketch([], False)
        if dragging_handle:
            self.window.update_overlay()  # the handles back where they are
        return busy


class HandTool(Tool):
    name, label, shortcut = "hand", "Hand", "H"
    edits = False
    cursor = Qt.CursorShape.OpenHandCursor
    icon = "hand"

    def activate(self) -> None:
        self.window.set_left_pans(True)
        super().activate()

    def deactivate(self) -> None:
        self.window.set_left_pans(False)

    def hint(self) -> str:
        return "Drag to pan (Space + drag pans in every tool)"


class MoveTool(Tool):
    """Move the selection from a base point to a destination, both snapping to points."""

    name, label, shortcut = "move", "Move", "M"
    cursor = Qt.CursorShape.CrossCursor
    gizmo, hovers, icon = "move", True, "move"

    def reset(self) -> None:
        self._base: tuple[float, float, str | None] | None = None
        self._plan: DragPlan | None = None
        self._snap: Candidate | None = None
        self._candidates: list[Candidate] | None = None

    @property
    def busy(self) -> bool:
        return self._base is not None or self.gizmo_dragging

    def hint(self) -> str:
        if not self.window.selection:
            return "Move: click a shape to move"
        if self._base is None:
            return (
                "Move: drag an arrow of the gizmo, or click a base point (snaps to shape "
                "points; Ctrl: no snapping)"
            )
        return "Move: click where the base point goes (Esc cancels)"

    def _points(self) -> list[Candidate]:
        if self._candidates is None:
            self._candidates = self.document.results.all_points()
        return self._candidates

    def press(self, x, y, modifiers) -> None:
        if not self.window.selection:  # nothing to move yet: this click selects
            self.window.select_click(self.window.hit(x, y), False)
            self.window.prompt(self.hint())
            return
        if self._base is None and self.gizmo_press(x, y):
            return
        if self._base is None:
            if not self.editable():
                return
            bx, by, label = self.snap(x, y, self._points(), modifiers)
            self._plan = self.document.moves.plan_drag(self.window.selection)
            if not self._plan.roots:
                return
            self._base = (bx, by, label)
            self.canvas.show_drag_preview(self._plan.preview, self.window.layers.colors)
            self.window.update_overlay()
            self.window.prompt(self.hint())
            return
        dx, dy = self._destination(x, y, modifiers)
        roots = self._plan.roots
        self.cancel()
        self.window.run(lambda: self.document.moves.move(roots, dx, dy))
        self.window.prompt(self.hint())

    def _destination(self, x, y, modifiers) -> tuple[float, float]:
        tx, ty, label = self.snap(x, y, self._plan.targets, modifiers)
        self._snap = (label, tx, ty) if label else None
        return tx - self._base[0], ty - self._base[1]

    def move(self, x, y, modifiers, left) -> None:
        if self.gizmo_dragging:
            if left:
                self.gizmo_move(x, y, modifiers)
            return
        if self._base is None:
            return
        dx, dy = self._destination(x, y, modifiers)
        self.canvas.move_drag_preview(dx, dy)
        self.canvas.show_points("snap", [self._snap] if self._snap else [])
        self.window.live(
            f"Move Δx {dx:g} µm, Δy {dy:g} µm   (click to place, Esc cancels)",
            _offset_label(dx, dy, self._snap[0] if self._snap else None),
        )

    def hover_label(self, x, y) -> str | None:
        if self._base is None and self.window.selection:
            return self.snap(x, y, self._points(), NONE, grid=False)[2]
        return None

    def release(self, x, y, modifiers) -> None:
        if self.gizmo_dragging:
            self.gizmo_release(x, y, modifiers)

    def markers(self) -> dict[str, list[Candidate]]:
        return {"anchor": [("base", self._base[0], self._base[1])]} if self._base else {}

    def cancel(self) -> bool:
        busy = self.busy
        self.gizmo_cancel()
        super().cancel()
        self.canvas.show_points("snap", [])
        return busy


class RotateTool(Tool):
    """Rotate the selection: drag anywhere to turn it about its centre.

    The angle follows the mouse smoothly; Ctrl snaps it to the angle step.
    Shift+click sets another pivot (a shape's point, or the grid) for the drags
    that follow; Esc goes back to the centre. A click without dragging selects
    what is under it.
    """

    name, label, shortcut = "rotate", "Rotate", "R"
    cursor = Qt.CursorShape.CrossCursor
    gizmo, hovers, icon = "rotate", True, "rotate"

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.pivot: tuple[float, float] | None = None  # Shift+click; None: the centre

    def reset(self) -> None:
        self._plan: DragPlan | None = None
        self._origin: tuple[float, float] | None = None  # the turn's centre, while dragging
        self._press: tuple[float, float] | None = None
        self._grab = 0.0  # the angle the drag started at, around the centre
        self._angle = 0.0
        self._candidates: list[Candidate] | None = None

    def activate(self) -> None:
        self.pivot = None
        super().activate()

    @property
    def busy(self) -> bool:
        return self._origin is not None

    @property
    def step(self) -> float:
        return self.setting("snapping/angle_step", ANGLE_STEP)

    def hint(self) -> str:
        if not self.window.selection:
            return "Rotate: click a shape to rotate"
        about = "the pivot" if self.pivot is not None else "its centre"
        return (
            f"Rotate: drag to turn it about {about} (Ctrl: {self.step:g}° steps); "
            "Shift+click: another pivot"
        )

    def _centre(self) -> tuple[float, float] | None:
        if self.pivot is not None:
            return self.pivot
        return self.document.results.selection_center(self.window.selection)

    def press(self, x, y, modifiers) -> None:
        if not self.window.selection:
            self.window.select_click(self.window.hit(x, y), False)
            self.window.prompt(self.hint())
            return
        if modifiers & SHIFT:  # another pivot
            if self._candidates is None:
                self._candidates = self.document.results.all_points()
            px, py, _ = self.snap(x, y, self._candidates, modifiers & ~SHIFT)
            self.pivot = (px, py)
            self.window.update_overlay()
            self.window.prompt(self.hint())
            return
        centre = self._centre()
        if centre is None or not self.editable():
            return
        self._plan = self.document.moves.plan_drag(self.window.selection)
        if not self._plan.roots:
            self._plan = None
            return
        self._origin, self._press = centre, (x, y)
        self._grab = math.degrees(math.atan2(y - centre[1], x - centre[0]))
        self._angle = 0.0
        self.canvas.set_gizmo_active("ring")
        self.canvas.show_drag_preview(self._plan.preview, self.window.layers.colors)

    def _angle_at(self, x, y, modifiers) -> float:
        cx, cy = self._origin
        if math.hypot(x - cx, y - cy) * self.canvas.pixels_per_um() < DRAG_THRESHOLD_PX:
            return self._angle  # too close to the centre to tell: as it was
        angle = math.degrees(math.atan2(y - cy, x - cx))
        angle = (angle - self._grab + 180) % 360 - 180
        if modifiers & CTRL:
            angle = round(angle / self.step) * self.step
        return round(angle, 6)

    def _dragged(self, x, y) -> bool:
        px, py = self._press
        return math.hypot(x - px, y - py) * self.canvas.pixels_per_um() >= DRAG_THRESHOLD_PX

    def move(self, x, y, modifiers, left) -> None:
        if self._origin is None or not left:
            return
        self._angle = self._angle_at(x, y, modifiers)
        self.canvas.rotate_drag_preview(self._angle, self._origin)
        self.canvas.set_gizmo_sweep(self._grab, self._angle)
        snap = "Ctrl: steps" if not modifiers & CTRL else f"{self.step:g}° steps"
        self.window.live(
            f"Rotate by {self._angle:g}°   ({snap}; release to apply, Esc cancels)",
            f"{self._angle:+g}°",
        )

    def release(self, x, y, modifiers) -> None:
        if self._origin is None:
            return
        dragged = self._dragged(x, y)
        angle = self._angle_at(x, y, modifiers) if dragged else 0.0
        origin, roots = self._origin, self._plan.roots
        self.cancel()
        if not dragged:  # a click: select what is under it
            self.window.select_click(self.window.hit(x, y), False)
        elif angle:
            self.window.run(lambda: self.document.moves.rotate(roots, angle, origin))
        self.window.prompt(self.hint())

    def cancel(self) -> bool:
        """Esc: stop the drag; when idle, go back to rotating about the centre."""
        if self._origin is not None:
            self.canvas.set_gizmo_active(None)
            return super().cancel()
        if self.pivot is not None:
            self.pivot = None
            self.window.update_overlay()
            return True
        return super().cancel()

    def design_changed(self) -> None:
        if self._origin is not None:
            self.canvas.set_gizmo_active(None)
        super().cancel()  # the drag stops; a chosen pivot stays

    def markers(self) -> dict[str, list[Candidate]]:
        return {"anchor": [("pivot", *self.pivot)]} if self.pivot else {}


class AlignTool(Tool):
    """Pick a shape, one of its points, then the point of another shape to put it on."""

    name, label, shortcut = "align", "Align", "A"
    cursor = Qt.CursorShape.CrossCursor
    hovers, icon = True, "align"

    def reset(self) -> None:
        self.step: str | None = None  # None (pick a shape), "own" or "target"
        self.path: NodePath | None = None
        self.point: str | None = None
        self.candidates: list[Candidate] = []
        # Picking one end only (a pick button in Properties): "point" or "to", and the
        # target to keep when only the point is picked.
        self.only: str | None = None
        self.keep_to: str | None = None

    @property
    def busy(self) -> bool:
        return self.step is not None

    def activate(self) -> None:
        super().activate()
        if len(self.window.selection) == 1 and self.step is None:
            self.begin(self.window.selection[0])

    def hint(self) -> str:
        name = self.document.node(self.path).name if self.path else None
        match self.step:
            case "own":
                return f"Align {name}: click the point of {name} to align (Esc cancels)"
            case "target":
                return f"Now click the point to put {self.point} on (Esc cancels)"
        return "Align: click the shape to align"

    def begin(
        self,
        path: NodePath,
        only: str | None = None,
        point: str | None = None,
        to: str | None = None,
    ) -> None:
        """Start aligning the shape at ``path``: its point, then the target. With
        ``only`` ("point" or "to") just that end is picked: the other (``to`` or
        ``point``) and the offset stay."""
        if not self.editable():
            return
        self.reset()
        self.only, self.keep_to = only, to or None
        if only == "to":
            targets = self._targets(path)
            if not targets:
                self.window.report_error("there is no other named shape here to align to")
                return
            self.step, self.path, self.point, self.candidates = (
                "target",
                path,
                point or "center",
                targets,
            )
        else:
            candidates = self.document.results.node_points(path)
            if not candidates:
                self.window.report_error(
                    "this shape has no points to align (does it have geometry?)"
                )
                return
            self.step, self.path, self.candidates = "own", path, candidates
        self.window.prompt(self.hint())
        self.window.update_overlay()

    def _targets(self, path: NodePath) -> list[Candidate]:
        return [(n, tx, ty) for n, _, tx, ty in self.document.results.align_targets(path)]

    def press(self, x, y, modifiers) -> None:
        if self.step is None:
            hit = self.window.hit(x, y)
            self.window.select_click(hit, False)
            if hit is not None:
                self.begin(hit)
            return
        picked = self._nearest(x, y)
        if picked is None:
            self.window.statusBar().showMessage(
                "Click on one of the marked points (Esc cancels)", 5000
            )
            return
        if self.step == "own" and self.only == "point" and self.keep_to:
            self._finish(picked, self.keep_to)
            return
        if self.step == "own":
            targets = self._targets(self.path)
            if not targets:
                self.reset()
                self.window.report_error("there is no other named shape here to align to")
                return
            self.step, self.point, self.candidates = "target", picked, targets
            self.window.prompt(self.hint())
            self.window.update_overlay()
            return
        self._finish(self.point, picked)

    def _finish(self, point: str, to: str) -> None:
        """Align ``point`` to ``to``; picking one end keeps the offset."""
        path, only = self.path, self.only
        old = self.document.node(path).align
        keep = only is not None and old is not None
        align = (
            Align(point=point, to=to, dx=old.dx, dy=old.dy) if keep else Align(point=point, to=to)
        )
        self.reset()
        self.canvas.set_cursor_label(None)
        self.window.update_overlay()
        ok, _ = self.window.run(lambda: self.document.nodes.set_align(path, align))
        if ok:
            self.window.notify(f"Aligned {point} to {to}", "success")
        if only is not None:
            self.window.set_tool("select")  # picked from Properties: back where it was

    def move(self, x, y, modifiers, left) -> None:
        if self.step is not None:  # the point a click would pick, by the cursor
            self.canvas.set_cursor_label(self._nearest(x, y))

    def _nearest(self, x, y) -> str | None:
        label = self.snap(x, y, self.candidates, NONE, grid=False)[2]
        return label

    def hover_label(self, x, y) -> str | None:
        return self._nearest(x, y) if self.step else None

    def markers(self) -> dict[str, list[Candidate]]:
        return {"pick": self.candidates} if self.step else {}

    def cancel(self) -> bool:
        only = self.only
        busy = super().cancel()
        if busy:
            self.window.update_overlay()
        if busy and only is not None:  # picked from Properties: back where it was
            self.window.set_tool("select")
        return busy


class CornersTool(Tool):
    """Round corners: pick a shape, then click its corners.

    A click rounds a corner (with the last radius used) or, on a rounded one,
    makes it sharp again; dragging away from a corner sets its radius. Each
    rounded corner has a handle on the bisector at its radius from the corner,
    with the radius by it: drag it to change the radius. While dragging only the
    rounding is drawn (nothing is rebuilt) and the radius stops at the largest
    that fits; the shape is rebuilt on release. A corner whose radius does not
    fit (the shape changed under it) is ringed in the error colour. The corners
    go into the shape's corners modifier, recorded so that they follow the
    design (see :mod:`mems_sketch.editing.corners`).
    """

    name, label, shortcut = "corners", "Corners", "O"
    cursor = Qt.CursorShape.CrossCursor
    hovers, icon = True, "fillet"
    DRAG_PX = 4  # a press that moves further is a drag (sets the radius)

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.radius: float = 1.0  # the last radius used, for the next corner

    def reset(self) -> None:
        self.path: NodePath | None = None
        self.candidates: list[tuple[float, float]] = []
        self.rounded: list[tuple[int, tuple[float, float]]] = []
        self.shown: list = []  # RoundedCorner: the handles
        self._press: tuple[float, float] | None = None  # the corner a press is on
        self._on_handle = False  # the press was on a rounded corner's handle
        self._shape = None  # the pressed corner's CornerShape, for the preview
        self._style = "round"
        self._dragged: float | None = None  # the radius being dragged
        self._limit: float | None = None  # the largest radius that corner takes
        if hasattr(self, "window"):
            self.canvas.show_corner_handles([])
            self.canvas.show_corner_preview([])

    @property
    def busy(self) -> bool:
        return self.path is not None

    def activate(self) -> None:
        super().activate()
        if len(self.window.selection) == 1:
            self.begin(self.window.selection[0])

    def deactivate(self) -> None:
        super().deactivate()
        self.canvas.show_corner_handles([])
        self.canvas.show_corner_preview([])

    def design_changed(self) -> None:
        """Stay on the shape (its corners may have moved or gone), unless it is gone."""
        self._press = self._dragged = None
        self.canvas.show_corner_preview([])
        if self.path is not None and not self._refresh():
            self.reset()

    def hint(self) -> str:
        if self.path is None:
            return "Corners: click the shape whose corners to round"
        name = self.document.node(self.path).name or self.document.node(self.path).kind
        return (
            f"Corners of {name}: click a corner to round it (again: sharp), drag from it "
            "or its handle to set the radius; Esc when done"
        )

    def begin(self, path: NodePath) -> None:
        if not self.editable():
            return
        self.path = path
        if not self._refresh() or not self.candidates:
            self.reset()
            self.window.report_error("this shape has no corners to round (does it build?)")
            return
        self.window.prompt(self.hint())
        self.window.update_overlay()

    def _refresh(self) -> bool:
        if self.path is None:
            return False
        try:
            self.candidates = self.document.corners.candidates(self.path)
            self.shown = self.document.corners.shown(self.path)
            self.rounded = self.document.corners.rounded(self.path)
        except (ValueError, KeyError, IndexError):  # e.g. undone away
            return False
        self._show_handles()
        return True

    def _show_handles(self, dragged: tuple[tuple[float, float], float] | None = None) -> None:
        """A handle per rounded corner (the dragged one where the drag has it)."""
        handles = []
        for corner in self.shown:
            radius = corner.radius
            if dragged is not None and _close(corner.shape.point, dragged[0]):
                radius = dragged[1]
            handles.append(_handle(corner.shape, radius, corner.style, corner.fits))
        new = dragged is not None and not any(_close(c.shape.point, dragged[0]) for c in self.shown)
        if new and self._shape is not None:  # a corner being rounded for the first time
            handles.append(_handle(self._shape, dragged[1], self._style, True))
        self.canvas.show_corner_handles(handles)

    def _corner_at(self, x: float, y: float) -> tuple[tuple[float, float] | None, bool]:
        """The corner a press at ``(x, y)`` is on, and whether it was on its handle."""
        best, found, handle = self.reach, None, False
        for corner in self.shown:
            hx, hy = corner.shape.handle(corner.radius)
            distance = math.hypot(hx - x, hy - y)
            if distance < best:
                best, found, handle = distance, corner.shape.point, True
        for px, py in self.candidates + [p for _, p in self.rounded]:
            distance = math.hypot(px - x, py - y)
            if distance < best:
                best, found, handle = distance, (px, py), False
        return found, handle

    def press(self, x, y, modifiers) -> None:
        corner, on_handle = self._corner_at(x, y) if self.path is not None else (None, False)
        if corner is None:
            hit = self.window.hit(x, y)
            self.window.select_click(hit, False)
            if hit is not None:
                self.begin(hit)
            return
        self._press, self._on_handle, self._dragged = corner, on_handle, None
        existing = next((c for c in self.shown if _close(c.shape.point, corner)), None)
        self._style = existing.style if existing is not None else "round"
        try:  # worked out once: the drag stops at the largest radius that fits
            self._limit = self.document.corners.max_radius(self.path, *corner)
            self._shape = (
                existing.shape if existing else self.document.corners.shape(self.path, *corner)
            )
        except (ValueError, KeyError, IndexError):
            self._limit, self._shape = None, None

    def move(self, x, y, modifiers, left) -> None:
        if self._press is None or not left:
            return
        distance = math.hypot(x - self._press[0], y - self._press[1])
        if self._dragged is None and distance * self.canvas.pixels_per_um() < self.DRAG_PX:
            return
        radius = distance if modifiers & CTRL else max(round(distance, 1), 0.1)
        largest = math.floor(self._limit * 1e6) / 1e6 if self._limit is not None else None
        if largest is not None and radius >= largest:
            radius = largest
        if radius == self._dragged:
            return  # nothing new to show
        self._dragged = radius
        note = "the largest that fits" if radius == largest else "Ctrl: exact"
        self.window.live(f"Radius {radius:g} µm   (release to set; {note})", f"r {radius:g} µm")
        # Only the rounding is drawn while dragging; the shape is built on release.
        if self._shape is not None:
            self.canvas.show_corner_preview(self._shape.arc(radius, self._style))
        self._show_handles((self._press, radius))

    def release(self, x, y, modifiers) -> None:
        corner, radius, on_handle = self._press, self._dragged, self._on_handle
        self._press = self._dragged = None
        self._on_handle = False
        self.canvas.show_corner_preview([])
        if corner is None:
            return
        path = self.path
        existing = self.document.corners.at(path, *corner)
        if radius is not None:
            self.radius = _um(radius)
            self.window.run(
                lambda: self.document.corners.add(path, *corner, self.radius, self._style)
            )
        elif on_handle:
            self._show_handles()  # a click on a handle changes nothing
        elif existing is not None:
            self.window.run(lambda: self.document.corners.remove(path, existing))
        else:
            self.window.run(lambda: self.document.corners.add(path, *corner, self.radius))
        self.window.prompt(self.hint())

    def markers(self) -> dict[str, list[Candidate]]:
        if self.path is None:
            return {}
        rounded = {p for _, p in self.rounded}
        sharp = [("corner", x, y) for x, y in self.candidates if (x, y) not in rounded]
        return {"pick": sharp, "anchor": [("rounded", x, y) for x, y in rounded]}

    def cancel(self) -> bool:
        busy = super().cancel()
        self.canvas.show_corner_handles([])
        self.canvas.show_corner_preview([])
        return busy


def _close(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return abs(a[0] - b[0]) <= 1e-6 and abs(a[1] - b[1]) <= 1e-6


def _handle(shape, radius: float, style: str, fits: bool):
    """A rounded corner's handle for the canvas: on the bisector, at its radius."""
    from mems_sketch.gui.canvas import CornerHandle

    letter = "c" if style == "chamfer" else "r"
    label = f"{letter} {radius:g} µm" + ("" if fits else " — too large")
    return CornerHandle(shape.point, shape.handle(radius), label, fits)


DISTANCE, ANGLE = "distance", "angle"  # the Measure tool's modes
MEASURE_MODE_KEY = "measure/mode"  # app setting: the mode it was last used in


class SnapTargets:
    """What a measurement snaps to, best first: a shape's named point, a corner of
    anything drawn, else the nearest point of an edge. Placed components count
    too, as they are placed (the geometry is flattened into the component's frame).
    """

    def __init__(self, points: list[Candidate], geometry=None) -> None:
        self.points = points
        corners, starts, ends = [], [], []
        if geometry is not None:
            for layer in geometry.layer_names():
                for polygon in geometry.polygons(layer):
                    for ring in (polygon.hull, *polygon.holes):
                        loop = np.asarray(ring, dtype=float).reshape(-1, 2)
                        if len(loop) < 2:
                            continue
                        corners.append(loop)
                        starts.append(loop)
                        ends.append(np.roll(loop, -1, axis=0))
        empty = np.empty((0, 2))
        self.corners = np.concatenate(corners) if corners else empty
        self.starts = np.concatenate(starts) if starts else empty
        self.ends = np.concatenate(ends) if ends else empty

    def nearest(self, x: float, y: float, reach: float) -> tuple[float, float, str] | None:
        """The point to snap ``(x, y)`` to within ``reach`` µm, with what it is: the
        point's name, ``corner`` or ``edge``; None when nothing is that close."""
        best = None
        for label, px, py in self.points:
            distance = math.hypot(px - x, py - y)
            if distance < reach and (best is None or distance < best[0]):
                best = (distance, px, py, label)
        if best is not None:
            return best[1], best[2], best[3]
        if len(self.corners):
            distances = np.hypot(self.corners[:, 0] - x, self.corners[:, 1] - y)
            index = int(np.argmin(distances))
            if distances[index] < reach:
                cx, cy = self.corners[index]
                return float(cx), float(cy), "corner"
        if len(self.starts):
            along = self.ends - self.starts
            lengths = np.einsum("ij,ij->i", along, along)
            lengths[lengths == 0] = 1.0
            where = np.array([x, y])
            t = np.clip(np.einsum("ij,ij->i", where - self.starts, along) / lengths, 0, 1)
            feet = self.starts + along * t[:, None]
            distances = np.hypot(feet[:, 0] - x, feet[:, 1] - y)
            index = int(np.argmin(distances))
            if distances[index] < reach:
                fx, fy = feet[index]
                return float(fx), float(fy), "edge"
        return None


class MeasureTool(Tool):
    """Measure a distance (two clicks) or an angle (the vertex, then a point on
    each arm); the rulers stay, per component, until cleared.

    Points snap to the shapes' points, to the corners and edges of everything
    drawn (placed components too) and else to the grid; a dot shows where. The
    mode is switched in the status bar, or with D (distance) and N (angle).
    """

    name, label, shortcut = "measure", "Measure", "D"
    edits = False
    cursor = Qt.CursorShape.CrossCursor
    icon = "measure"

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        settings = getattr(window, "settings", None)
        mode = settings.value(MEASURE_MODE_KEY, DISTANCE) if settings is not None else DISTANCE
        self.mode = mode if mode in (DISTANCE, ANGLE) else DISTANCE

    def reset(self) -> None:
        self._placed: list[tuple[float, float]] = []  # the points clicked so far
        self._targets: SnapTargets | None = None

    @property
    def busy(self) -> bool:
        return bool(self._placed)

    @property
    def needed(self) -> int:
        return 2 if self.mode == DISTANCE else 3

    def set_mode(self, mode: str) -> None:
        """Measure distances or angles (what was being measured is dropped)."""
        if mode not in (DISTANCE, ANGLE):
            raise ValueError(f"unknown measure mode '{mode}'")
        if mode != self.mode:
            self.cancel()
        self.mode = mode
        settings = getattr(self.window, "settings", None)
        if settings is not None:
            settings.set_value(MEASURE_MODE_KEY, mode)
        if self.window.tool is self:
            self.window.prompt(self.hint())
            self.window.tool_status.show_for(self)

    def hint(self) -> str:
        snapping = "snaps to points, corners and edges; Ctrl: no snapping"
        if self.mode == DISTANCE:
            if not self._placed:
                return f"Measure: click the first point ({snapping}; N: angles)"
            return "Measure: click the second point (Esc cancels)"
        if not self._placed:
            return f"Angle: click the vertex ({snapping}; D: distances)"
        if len(self._placed) == 1:
            return "Angle: click a point on the first arm (Esc cancels)"
        return "Angle: click a point on the second arm (Shift: 15° steps, Esc cancels)"

    def targets(self) -> SnapTargets:
        if self._targets is None:
            results = self.document.results
            try:
                geometry = results.geometry()
            except Exception:  # noqa: BLE001 - it does not build: the named points only
                geometry = None
            self._targets = SnapTargets(results.all_points(), geometry)
        return self._targets

    def locate(self, x, y, modifiers) -> tuple[float, float, str | None]:
        """Where a click at ``(x, y)`` puts a point, and what it snapped to."""
        if modifiers & CTRL:
            return x, y, None
        if self.setting("snapping/points", True):
            found = self.targets().nearest(x, y, self.reach)
            if found is not None:
                return found
        if self.setting("snapping/grid", True):
            step = self.canvas.grid_step()
            return _um(round(x / step) * step), _um(round(y / step) * step), None
        return x, y, None

    def _point(self, x, y, modifiers) -> tuple[float, float, str | None]:
        px, py, label = self.locate(x, y, modifiers)
        if self.mode == ANGLE and len(self._placed) == 2 and modifiers & SHIFT:
            (vx, vy), (ax, ay) = self._placed  # the second arm in 15° steps
            first = math.atan2(ay - vy, ax - vx)
            turn = math.atan2(py - vy, px - vx) - first
            turn = math.radians(round(math.degrees(turn) / 15) * 15)
            reach = math.hypot(px - vx, py - vy)
            px, py = vx + reach * math.cos(first + turn), vy + reach * math.sin(first + turn)
            label = None
        return px, py, label

    def press(self, x, y, modifiers) -> None:
        px, py, _ = self._point(x, y, modifiers)
        if self.mode == ANGLE and self._placed and (px, py) == self._placed[0]:
            return  # an arm needs a point other than the vertex
        self._placed.append((px, py))
        if len(self._placed) < self.needed:
            self.window.prompt(self.hint())
            return
        ruler = tuple(v for point in self._placed for v in point)
        self.reset()
        self.canvas.show_snap_marker(None)
        self.window.add_ruler(ruler)
        self.window.prompt(self.hint())

    def move(self, x, y, modifiers, left) -> None:
        px, py, label = self._point(x, y, modifiers)
        kind = None if label is None else "edge" if label == "edge" else "point"
        self.canvas.show_snap_marker((px, py) if kind else None, kind)
        if not self._placed:
            return
        self.window.draw_rulers(extra=(*(v for point in self._placed for v in point), px, py))
        if self.mode == DISTANCE:
            dx, dy = px - self._placed[0][0], py - self._placed[0][1]
            self.window.prompt(f"Distance {math.hypot(dx, dy):.3f} µm  (dx {dx:.3f}, dy {dy:.3f})")
        elif len(self._placed) == 2:
            sweep = angle_between(self._placed[0], self._placed[1], (px, py))[1]
            self.window.prompt(f"Angle {sweep:.2f}°")

    def hover_label(self, x, y) -> str | None:
        return self.locate(x, y, NONE)[2]

    def markers(self) -> dict[str, list[Candidate]]:
        names = ("start",) if self.mode == DISTANCE else ("vertex", "arm")
        return {"anchor": [(n, *p) for n, p in zip(names, self._placed, strict=False)]}

    def design_changed(self) -> None:
        super().design_changed()
        self._targets = None  # the geometry changed

    def cancel(self) -> bool:
        busy = super().cancel()
        self.canvas.show_snap_marker(None)
        self.window.draw_rulers()
        return busy


class DrawTool(Tool):
    """Base of the drawing tools: points snap to shape points, else to the grid."""

    draws = True
    uses_layer: ClassVar[bool] = True  # draws on the chosen layer (a guide does not)
    cursor = Qt.CursorShape.CrossCursor
    noun: ClassVar[str]
    once = False  # started from Add: back to Select after one shape (see draw_once)

    def deactivate(self) -> None:
        super().deactivate()
        self.once = False

    def reset(self) -> None:
        self.placed: list[tuple[float, float]] = []
        self._snap: Candidate | None = None
        self._candidates: list[Candidate] | None = None

    @property
    def busy(self) -> bool:
        return bool(self.placed)

    def _points(self) -> list[Candidate]:
        if self._candidates is None:
            self._candidates = self.document.results.all_points()
        mine = [(f"point {i + 1}", x, y) for i, (x, y) in enumerate(self.placed)]
        return self._candidates + mine

    def locate(self, x: float, y: float, modifiers) -> tuple[float, float]:
        """Where a click at ``(x, y)`` puts a point: snapped, and with Shift constrained."""
        px, py, label = self.snap(x, y, self._points(), modifiers)
        self._snap = (label, px, py) if label else None
        if modifiers & SHIFT and self.placed:
            px, py = self.constrain(self.placed[-1], (px, py))
        return _um(px), _um(py)

    def constrain(self, anchor, point) -> tuple[float, float]:
        """Shift: the point on the nearest 45° line through the previous point."""
        ax, ay = anchor
        dx, dy = point[0] - ax, point[1] - ay
        angle = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
        ux, uy = round(math.cos(angle), 12), round(math.sin(angle), 12)
        length = dx * ux + dy * uy
        return ax + length * ux, ay + length * uy

    def start(self) -> bool:
        """Checks before the first point: the tab can be edited and a layer is chosen."""
        if not self.editable():
            return False
        if self.uses_layer and self.window.draw_layer is None:
            self.window.report_error("add a layer to draw on first")
            return False
        return True

    def preview(self, shape: Shape | None, outline: list[tuple[float, float]], closed: bool):
        """Show the shape as it would be drawn, plus the outline of the placed points."""
        self.canvas.show_points("snap", [self._snap] if self._snap else [])
        self.canvas.show_sketch(outline, closed)
        if shape is None:
            self.canvas.clear_drag_preview()
            return
        try:
            geometry = build_shapes([shape])
        except Exception:  # noqa: BLE001 - e.g. a degenerate shape: just no fill
            self.canvas.clear_drag_preview()
            return
        self.canvas.show_drag_preview(geometry, self.window.layers.colors)

    def add(self, shape: Shape | None, problem: str) -> None:
        """Add the finished shape to the component (and select it), or say what is wrong."""
        self.cancel()
        if shape is None:
            self.window.report_error(problem)
            return
        self.window.add_drawn(shape)
        if self.once:
            self.window.set_tool("select")
        else:
            self.window.prompt(self.hint())

    def hover_label(self, x, y) -> str | None:
        return self.snap(x, y, self._points(), NONE, grid=False)[2]

    def start_at(self, x: float, y: float) -> None:
        """Place the first point at ``(x, y)``, as a click there would (right-click › Add)."""
        self.press(x, y, NONE)

    def markers(self) -> dict[str, list[Candidate]]:
        return {"anchor": [(f"point {i + 1}", x, y) for i, (x, y) in enumerate(self.placed)]}

    def cancel(self) -> bool:
        busy = super().cancel()
        self.canvas.show_points("snap", [])
        self.canvas.show_sketch([], False)
        if busy:
            self.window.update_overlay()
        return busy


class _TwoPointTool(DrawTool):
    """Rectangle and circle: press, drag and release, or click twice."""

    def reset(self) -> None:
        super().reset()
        self._pressed_at: tuple[float, float] | None = None

    def shape(self, start, end, modifiers) -> Shape | None:
        raise NotImplementedError

    def describe(self, shape: Shape) -> str:
        raise NotImplementedError

    def press(self, x, y, modifiers) -> None:
        if not self.placed:
            if not self.start():
                return
            self.placed = [self.locate(x, y, modifiers)]
            self._pressed_at = (x, y)
            self.window.update_overlay()
            self.window.prompt(self.hint())
            return
        self._finish(x, y, modifiers)

    def move(self, x, y, modifiers, left) -> None:
        if not self.placed:
            self.locate(x, y, modifiers)
            self.canvas.show_points("snap", [self._snap] if self._snap else [])
            return
        shape = self.shape(self.placed[0], self.locate(x, y, modifiers), modifiers)
        self.preview(shape, [], False)
        if shape is not None:
            self.window.live(f"{self.describe(shape)}   (click to finish, Esc cancels)")

    def start_at(self, x: float, y: float) -> None:
        super().start_at(x, y)
        self._pressed_at = None  # no press to release: the next click finishes

    def release(self, x, y, modifiers) -> None:
        pressed, self._pressed_at = self._pressed_at, None
        if pressed is None or not self.placed:
            return
        dragged = math.hypot(x - pressed[0], y - pressed[1]) * self.canvas.pixels_per_um()
        if dragged >= DRAG_THRESHOLD_PX:  # press, drag, release: done
            self._finish(x, y, modifiers)

    def _finish(self, x, y, modifiers) -> None:
        shape = self.shape(self.placed[0], self.locate(x, y, modifiers), modifiers)
        self.add(shape, f"the {self.noun} has no area")


class RectTool(_TwoPointTool):
    name, label, shortcut, noun = "rect", "Rectangle", "B", "rectangle"
    icon = "rect"

    def hint(self) -> str:
        if not self.placed:
            return "Rectangle: drag, or click the first corner (snaps to points; Ctrl: no snapping)"
        return "Rectangle: click the opposite corner (Shift: square, Esc cancels)"

    def locate(self, x, y, modifiers) -> tuple[float, float]:
        if not (modifiers & SHIFT and self.placed):
            return super().locate(x, y, modifiers)
        px, py = super().locate(x, y, modifiers & ~SHIFT)
        (ax, ay), side = (
            self.placed[0],
            max(abs(px - self.placed[0][0]), abs(py - self.placed[0][1])),
        )
        return _um(ax + math.copysign(side, px - ax)), _um(ay + math.copysign(side, py - ay))

    def shape(self, start, end, modifiers) -> Shape | None:
        (x0, y0), (x1, y1) = start, end
        if x0 == x1 or y0 == y1:
            return None
        return RectShape(
            layer=self.window.draw_layer,
            x0=min(x0, x1),
            y0=min(y0, y1),
            x1=max(x0, x1),
            y1=max(y0, y1),
        )

    def describe(self, shape) -> str:
        return f"Rectangle {shape.x1 - shape.x0:g} × {shape.y1 - shape.y0:g} µm"


class CircleTool(_TwoPointTool):
    name, label, shortcut, noun = "circle", "Circle", "C", "circle"
    icon = "circle"

    def hint(self) -> str:
        if not self.placed:
            return "Circle: click the centre (snaps to points; Ctrl: no snapping)"
        return "Circle: click to set the radius (Esc cancels)"

    def shape(self, start, end, modifiers) -> Shape | None:
        radius = math.hypot(end[0] - start[0], end[1] - start[1])
        if self._snap is None and not modifiers & CTRL:  # a round radius, unless on a point
            step = self.canvas.grid_step()
            radius = round(radius / step) * step
        radius = _um(radius)
        if radius <= 0:
            return None
        return CircleShape(layer=self.window.draw_layer, x=start[0], y=start[1], radius=radius)

    def describe(self, shape) -> str:
        return f"Circle radius {shape.radius:g} µm"


class _PointsTool(DrawTool):
    """Polygon and path: click the points, then finish."""

    minimum: ClassVar[int]
    closes: ClassVar[bool] = False

    def reset(self) -> None:
        super().reset()
        self._cursor: tuple[float, float] | None = None

    def shape(self, points: list[tuple[float, float]]) -> Shape | None:
        raise NotImplementedError

    def hint(self) -> str:
        noun = self.noun.capitalize()
        if not self.placed:
            return f"{noun}: click the first point (snaps to points; Ctrl: no snapping)"
        return (
            f"{noun}: click the next point (Shift: 45°); double-click or Enter finishes, "
            "Backspace takes back a point, Esc cancels"
        )

    def press(self, x, y, modifiers) -> None:
        if not self.placed and not self.start():
            return
        point = self.locate(x, y, modifiers)
        if self.closes and len(self.placed) >= self.minimum and point == self.placed[0]:
            self.finish()  # clicked on the first point: closed
            return
        if not self.placed or point != self.placed[-1]:
            self.placed.append(point)
        self._cursor = point
        self._show()
        self.window.update_overlay()
        self.window.prompt(self.hint())

    def move(self, x, y, modifiers, left) -> None:
        self._cursor = self.locate(x, y, modifiers)
        if not self.placed:
            self.canvas.show_points("snap", [self._snap] if self._snap else [])
            return
        self._show()
        (ax, ay), (bx, by) = self.placed[-1], self._cursor
        if (ax, ay) != (bx, by):  # the edge being drawn, by the cursor
            angle = math.degrees(math.atan2(by - ay, bx - ax))
            self.canvas.set_cursor_label(f"{math.hypot(bx - ax, by - ay):g} µm  ∠ {angle:.1f}°")

    def _show(self) -> None:
        points = list(self.placed)
        if self._cursor is not None and self._cursor != points[-1]:
            points.append(self._cursor)
        shape = self.shape(points) if len(points) >= self.minimum else None
        self.preview(shape, points, self.closes)

    def double_click(self, x, y) -> bool:
        if self.placed:
            self.finish()
        return True  # never open a component while drawing

    def key(self, key) -> bool:
        if not self.placed:
            return False
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish()
            return True
        if key == Qt.Key.Key_Backspace:
            self.placed.pop()
            if self.placed:
                self._show()
                self.window.update_overlay()
            else:
                self.cancel()
            self.window.prompt(self.hint())
            return True
        return False

    def finish(self) -> None:
        points = self.placed
        shape = self.shape(points) if len(points) >= self.minimum else None
        self.add(shape, f"a {self.noun} needs at least {self.minimum} different points")


class PolygonTool(_PointsTool):
    name, label, shortcut, noun = "polygon", "Polygon", "P", "polygon"
    icon = "polygon"
    minimum, closes = 3, True

    def shape(self, points) -> Shape | None:
        return PolygonShape(layer=self.window.draw_layer, points=list(points))


class PathTool(_PointsTool):
    name, label, shortcut, noun = "path", "Path", "W", "path"
    icon = "path"
    minimum = 2

    def hint(self) -> str:
        return super().hint().replace("Path:", f"Path ({self.window.path_width:g} µm wide):", 1)

    def shape(self, points) -> Shape | None:
        return PathShape(
            layer=self.window.draw_layer, points=list(points), width=self.window.path_width
        )


class RouteTool(DrawTool):
    """A wire from port to port: click a port, any via points, then another port.

    The ports are marked; a click near one picks it (snapping or not), a click
    elsewhere places a via point (snapped as drawing does). The route is
    Manhattan between ports along x and y, else straight; it is as wide as the
    ports, or the path width when they have none or differ. With a route
    selected and none being drawn, its via points have handles: drag one to
    move it.
    """

    name, label, shortcut, noun = "route", "Route", "E", "route"
    icon = "route"
    uses_layer = False  # on its ports' layer

    def reset(self) -> None:
        super().reset()
        self.start_port: str | None = None
        self._ports = None  # reference -> Port, worked out once per design
        self._cursor: tuple[float, float] | None = None
        self._drag: tuple[NodePath, int] | None = None  # a selected route's via point
        self._dragged_to: tuple[float, float] | None = None

    @property
    def busy(self) -> bool:
        return self.start_port is not None or self._drag is not None

    def design_changed(self) -> None:
        self._ports = self._candidates = None

    def hint(self) -> str:
        if self.start_port is None:
            return "Route: click the port to start at (or drag a selected route's via point)"
        return (
            f"Route from {self.start_port}: click via points, then the port to end at; "
            "Backspace takes back a point, Esc cancels"
        )

    def ports(self) -> dict:
        if self._ports is None:
            try:
                self._ports = self.document.results.all_ports()
            except Exception:  # noqa: BLE001 - the design does not build: nothing to pick
                self._ports = {}
        return self._ports

    def _port_at(self, x: float, y: float) -> str | None:
        """The port a click at ``(x, y)`` picks: the nearest in reach, but not the start."""
        best, found = self.reach, None
        for name, port in self.ports().items():
            distance = math.hypot(port.x - x, port.y - y)
            if distance < best and name != self.start_port:
                best, found = distance, name
        return found

    def _route(self) -> tuple[NodePath, Shape] | None:
        """The selected shape whose via points can be dragged (a route)."""
        if len(self.window.selection) != 1:
            return None
        path = self.window.selection[0]
        node = self.document.node(path)
        return (path, node) if node.handles() and len(path) == 1 else None

    def _via_at(self, x: float, y: float) -> int | None:
        selected = self._route()
        if selected is None:
            return None
        best, found = self.reach, None
        for k, (vx, vy) in enumerate(self._vias(*selected)):
            distance = math.hypot(vx - x, vy - y)
            if distance < best:
                best, found = distance, k
        return found

    def _vias(self, path: NodePath, route: Shape) -> list[tuple[float, float]]:
        """Where its via points are (they may be expressions)."""
        scope = self.document.results.scope(path)
        try:
            return [(_value(px, scope), _value(py, scope)) for px, py in route.handles()]
        except (ValueError, KeyError):  # an expression it cannot work out here
            return []

    def press(self, x, y, modifiers) -> None:
        if self.start_port is None:
            via = self._via_at(x, y)
            if via is not None and self.editable():
                self._drag, self._dragged_to = (self._route()[0], via), None
                return
            port = self._port_at(x, y)
            if port is None:
                self.window.select_click(self.window.hit(x, y), False)  # a route to edit
                self.window.update_overlay()
                return
            if not self.start():
                return
            self.start_port = port
        else:
            port = self._port_at(x, y)
            if port is not None:
                self.finish(port)
                return
            point = self.locate(x, y, modifiers)
            if not self.placed or point != self.placed[-1]:
                self.placed.append(point)
        self._show()
        self.window.update_overlay()
        self.window.prompt(self.hint())

    def locate(self, x, y, modifiers) -> tuple[float, float]:
        start = self.ports().get(self.start_port) if self.start_port else None
        if modifiers & SHIFT and start is not None and not self.placed:  # 45° from the start
            return self.constrain((start.x, start.y), self.locate(x, y, modifiers & ~SHIFT))
        return super().locate(x, y, modifiers)

    def move(self, x, y, modifiers, left) -> None:
        if self._drag is not None:
            if left:
                self._dragged_to = self.locate(x, y, modifiers)
                path, k = self._drag
                route = self.document.node(path)
                vias = self._vias(path, route)
                vias[k] = self._dragged_to
                ends = self.document.results.node_ports(path)
                outline = [(p.x, p.y) for p in (ends.get("start"),) if p]
                outline += vias + [(p.x, p.y) for p in (ends.get("end"),) if p]
                self.canvas.show_sketch(outline, False)
                self.canvas.set_cursor_label(
                    f"via {k + 1}: {self._dragged_to[0]:g}, {self._dragged_to[1]:g}"
                )
            return
        port = self._port_at(x, y)
        self.canvas.set_cursor_label(port)
        if self.start_port is None:
            return
        if port is not None:
            self._cursor = (self.ports()[port].x, self.ports()[port].y)
            self._snap = None
        else:
            self._cursor = self.locate(x, y, modifiers)
        self._show()

    def _show(self) -> None:
        start = self.ports().get(self.start_port)
        if start is None:  # gone since it was picked (undone, renamed)
            self.cancel()
            self.window.prompt(self.hint())
            return
        points = [(start.x, start.y), *self.placed]
        if self._cursor is not None and self._cursor != points[-1]:
            points.append(self._cursor)
        self.canvas.show_points("snap", [self._snap] if self._snap else [])
        self.canvas.show_sketch(points, False)

    def release(self, x, y, modifiers) -> None:
        drag, to = self._drag, self._dragged_to
        self._drag = self._dragged_to = None
        if drag is None:
            return
        self.canvas.show_sketch([], False)
        self.canvas.set_cursor_label(None)
        if to is None:
            return  # a click on a handle changes nothing
        path, k = drag
        route = self.document.node(path)
        moved = route.with_handle(k, to)
        ok, _ = self.window.run(lambda: self.document.nodes.replace(path, moved))
        if ok:
            self.window.notify(f"Moved via point {k + 1} of {route.name}", "success")

    def double_click(self, x, y) -> bool:
        return self.start_port is not None  # never open a component while routing

    def key(self, key) -> bool:
        if self.start_port is None or key != Qt.Key.Key_Backspace:
            return False
        if self.placed:
            self.placed.pop()
            self._show()
            self.window.update_overlay()
        else:
            self.cancel()
        self.window.prompt(self.hint())
        return True

    def finish(self, end: str) -> None:
        start, ports = self.start_port, self.ports()
        if start not in ports:
            self.add(None, f"the port {start} is gone: pick the start again")
            return
        axis = all(round(ports[p].angle, 6) % 90 == 0 for p in (start, end))
        widths = {ports[p].width for p in (start, end) if ports[p].width > 0}
        shape = RouteShape(
            start=start,
            end=end,
            style="manhattan" if axis else "straight",
            via=list(self.placed),
            width=0.0 if len(widths) == 1 else self.window.path_width,
        )
        self.add(shape, "")

    def markers(self) -> dict[str, list[Candidate]]:
        ports = [(n, p.x, p.y) for n, p in self.ports().items() if n != self.start_port]
        if self.start_port is None:
            selected = self._route()
            vias = self._vias(*selected) if selected else []
            return {
                "pick": ports,
                "anchor": [(f"via {k + 1}", x, y) for k, (x, y) in enumerate(vias)],
            }
        start = self.ports().get(self.start_port)
        anchors = [(self.start_port, start.x, start.y)] if start else []
        anchors += [(f"via {k + 1}", x, y) for k, (x, y) in enumerate(self.placed)]
        return {"pick": ports, "anchor": anchors}

    def cancel(self) -> bool:
        dragging = self._drag is not None
        busy = super().cancel()
        self.canvas.set_cursor_label(None)
        if dragging:
            self.window.update_overlay()
        return busy


def _handle_cursor(handle) -> Qt.CursorShape:
    """The cursor over a handle: a rectangle's side drags across it, a corner
    diagonally, the others any way."""
    keys = handle.key.split()
    if handle.role == "edge":
        return Qt.CursorShape.SizeHorCursor if keys[0][0] == "x" else Qt.CursorShape.SizeVerCursor
    if handle.role == "corner":
        rising = (keys[0] == "x1") == (keys[1] == "y1")  # bottom left or top right
        return Qt.CursorShape.SizeBDiagCursor if rising else Qt.CursorShape.SizeFDiagCursor
    return Qt.CursorShape.SizeAllCursor


def _value(value, scope: dict[str, float]) -> float:
    return float(value) if isinstance(value, int | float) else float(evaluate(value, scope))


class GuideTool(_TwoPointTool):
    """A construction line: drag, or click its two ends (Shift: 45° steps)."""

    name, label, shortcut, noun = "guide", "Guide", "G", "guide"
    icon = "guide"
    uses_layer = False

    def hint(self) -> str:
        if not self.placed:
            return "Guide: drag, or click where it starts (snaps to points; Ctrl: no snapping)"
        return "Guide: click where it ends (Shift: 45° steps, Esc cancels)"

    def shape(self, start, end, modifiers) -> Shape | None:
        if start == end:
            return None
        return GuideShape(x0=start[0], y0=start[1], x1=end[0], y1=end[1])

    def preview(self, shape, outline, closed) -> None:
        ends = [(shape.x0, shape.y0), (shape.x1, shape.y1)] if shape is not None else outline
        super().preview(None, ends, False)  # draws nothing: just its line

    def describe(self, shape) -> str:
        length = math.hypot(shape.x1 - shape.x0, shape.y1 - shape.y0)
        return f"Guide {length:g} µm, {shape.summary().removeprefix('guide ')}"

    def _finish(self, x, y, modifiers) -> None:
        shape = self.shape(self.placed[0], self.locate(x, y, modifiers), modifiers)
        self.add(shape, "a guide needs two different end points")


class PlaceTool(Tool):
    """Put down a component chosen in the Place popup or Search Everywhere: it
    follows the cursor, its origin snapping to points and the grid (Ctrl: not),
    until a click places it. Esc lets it go; either way Select comes back."""

    name, label, shortcut = "place", "Place component", ""
    listed = False
    cursor = Qt.CursorShape.CrossCursor
    icon = "place"

    def reset(self) -> None:
        self.component: str | None = None
        self.at: tuple[float, float] | None = None
        self.candidates: list[Candidate] = []

    @property
    def busy(self) -> bool:
        return self.component is not None

    def hint(self) -> str:
        if self.component is None:
            return "Choose a component to place (Shift+A)"
        return f"Click to place {self.component} (Esc cancels, Ctrl: no snapping)"

    def begin(self, component: str) -> bool:
        """Start placing ``component``; False (and a message) when it cannot be."""
        if not self.editable():
            return False
        try:
            geometry = self.document.results.geometry(component)
        except Exception as exc:  # noqa: BLE001 - it does not build: say why
            self.window.report_error(f"{component} cannot be placed: {exc}")
            return False
        self.reset()
        self.component = component
        try:
            self.candidates = list(self.document.results.all_points())
        except Exception:  # noqa: BLE001 - nothing to snap to but the grid
            self.candidates = []
        self.canvas.show_ghost(geometry, self.window.layers.colors)
        self.canvas.move_ghost(*self.canvas.cursor_um())
        self.canvas.set_cursor_label(component.rsplit(".", 1)[-1])
        self.window.prompt(self.hint())
        return True

    def move(self, x, y, modifiers, left) -> None:
        if self.component is None:
            return
        x, y, onto = self.snap(x, y, self.candidates, modifiers)
        self.at = (x, y)
        self.canvas.move_ghost(x, y)
        short = self.component.rsplit(".", 1)[-1]
        self.canvas.set_cursor_label(f"{short} → {onto}" if onto else short)

    def press(self, x, y, modifiers) -> None:
        if self.component is None:
            return
        x, y, _ = self.snap(x, y, self.candidates, modifiers)
        component = self.component
        self.reset()
        self.canvas.show_ghost(None)
        self.canvas.set_cursor_label(None)
        self.window.place_component_at(component, x, y)
        self.window.set_tool("select")

    def cancel(self) -> bool:
        busy = super().cancel()
        self.canvas.show_ghost(None)
        if busy and self.window.tool is self:
            self.window.set_tool("select")
        return busy

    def design_changed(self) -> None:
        """An edit elsewhere (e.g. an undo) keeps the component in hand."""


TOOLS: tuple[type[Tool], ...] = (
    SelectTool,
    HandTool,
    MoveTool,
    RotateTool,
    AlignTool,
    CornersTool,
    MeasureTool,
    RectTool,
    CircleTool,
    PolygonTool,
    PathTool,
    RouteTool,
    GuideTool,
    PlaceTool,
)


def _um(value: float) -> float:
    """A coordinate without floating-point noise (grid steps like 0.1 add up badly)."""
    return round(value, 6) + 0.0  # + 0.0 turns -0.0 into 0.0


def _offset_label(dx: float, dy: float, onto: str | None = None) -> str:
    """A move's offset by the cursor, and the point it snaps to."""
    text = f"Δx {dx:g}  Δy {dy:g} µm"
    return f"{text}  → {onto}" if onto else text
