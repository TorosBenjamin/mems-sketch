"""Canvas tools: what a left click or drag on the canvas does.

Zooming (wheel) and panning (middle or right drag, or left drag while Space
is held) work in every tool; the canvas handles those itself. Everything else
goes to the active tool:

* **Select** (V): click to select, drag the selection to move it, drag on
  empty space to select with a box
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

import contextlib
import math
from typing import TYPE_CHECKING, ClassVar

import klayout.db as kdb
from PySide6.QtCore import Qt

from mems_sketch.core.component import to_dbu
from mems_sketch.core.shapes import (
    Align,
    CircleShape,
    Evaluator,
    GuideShape,
    NodePath,
    PathShape,
    PolygonShape,
    RectShape,
    Shape,
)
from mems_sketch.gui.canvas import angle_between

if TYPE_CHECKING:
    from mems_sketch.editing import DragPlan
    from mems_sketch.gui.app import MainWindow

SNAP_PX = 10  # default: a point snaps to another within this many pixels
DRAG_THRESHOLD_PX = 4  # a press moving less than this is a click, not a drag
ANGLE_STEP = 15.0  # default: degrees the Rotate tool snaps to
CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
ALT = Qt.KeyboardModifier.AltModifier
NONE = Qt.KeyboardModifier.NoModifier

Candidate = tuple[str, float, float]  # label, x, y


class Tool:
    """Base class: override the event methods a tool needs."""

    name: ClassVar[str]
    label: ClassVar[str]
    shortcut: ClassVar[str]
    edits: ClassVar[bool] = True  # changes the design, so refused on read-only tabs
    draws: ClassVar[bool] = False  # a drawing tool (started from Add, not a canvas mode)
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
        step = self.canvas.grid_step()
        return round(dx / step) * step, round(dy / step) * step, None

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
        step = self.canvas.grid_step()
        snap = not modifiers & CTRL and self.setting("snapping/grid", True)
        along = dx if part == "x" else dy
        along = round(along / step) * step if snap else along
        return (along, 0.0) if part == "x" else (0.0, along)

    def gizmo_move(self, x, y, modifiers) -> None:
        dx, dy = self._gizmo_delta(x, y, modifiers)
        self.canvas.move_drag_preview(dx, dy)
        self.window.prompt(f"Move Δx {dx:g} µm, Δy {dy:g} µm   (Ctrl: no snapping)")

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
    name, label, shortcut = "select", "Select", "V"
    edits = False
    hovers, icon = True, "select"

    def reset(self) -> None:
        self._press: tuple[float, float] | None = None
        self._press_hit: NodePath | None = None
        self._pending: NodePath | None = None
        self._mode: str | None = None  # None, "move", "box" or "blocked"
        self._plan: DragPlan | None = None
        self._snap = None

    def hint(self) -> str:
        return "Click to select, drag to move, drag on empty space to select with a box"

    def press(self, x, y, modifiers) -> None:
        self.reset()
        if self.gizmo_press(x, y):
            return
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
            self.canvas.show_box((x0, y0, x, y))

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
        self.window.prompt(text)

    def release(self, x, y, modifiers) -> None:
        if self.gizmo_dragging:
            self.gizmo_release(x, y, modifiers)
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
                window.statusBar().showMessage(f"Aligned {point} to {target}", 5000)
            return
        detach = bool(modifiers & ALT)
        window.run(lambda: self.document.moves.move(plan.roots, dx, dy, detach=detach))

    @property
    def busy(self) -> bool:
        return self._mode in ("move", "box") or self.gizmo_dragging

    def cancel(self) -> bool:
        busy = self.busy
        self.gizmo_cancel()
        super().cancel()
        self.canvas.show_points("snap", [])
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
        self.window.prompt(f"Move Δx {dx:g} µm, Δy {dy:g} µm   (click to place, Esc cancels)")

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
    """Rotate the selection about a pivot; the angle snaps to 15° steps."""

    name, label, shortcut = "rotate", "Rotate", "R"
    cursor = Qt.CursorShape.CrossCursor
    gizmo, hovers, icon = "rotate", True, "rotate"

    def reset(self) -> None:
        self._pivot: tuple[float, float] | None = None
        self._plan: DragPlan | None = None
        self._angle = 0.0
        self._candidates: list[Candidate] | None = None
        self._ring: float | None = None  # dragging the ring: the angle it was grabbed at

    @property
    def busy(self) -> bool:
        return self._pivot is not None

    @property
    def step(self) -> float:
        return self.setting("snapping/angle_step", ANGLE_STEP)

    def hint(self) -> str:
        if not self.window.selection:
            return "Rotate: click a shape to rotate"
        if self._pivot is None:
            return "Rotate: drag the ring to rotate about the centre, or click a pivot"
        return (
            f"Rotate: move to set the angle ({self.step:g}° steps; Ctrl: free), click to apply, "
            "Esc cancels"
        )

    def press(self, x, y, modifiers) -> None:
        if not self.window.selection:
            self.window.select_click(self.window.hit(x, y), False)
            self.window.prompt(self.hint())
            return
        if self._pivot is None and self.canvas.gizmo_hit(x, y) == "ring":
            if not self.editable():
                return
            _, cx, cy = self.canvas.gizmo
            self._plan = self.document.moves.plan_drag(self.window.selection)
            if not self._plan.roots:
                return
            self._pivot = (cx, cy)
            self._ring = math.degrees(math.atan2(y - cy, x - cx))
            self.canvas.set_gizmo_active("ring")
            self.canvas.show_drag_preview(self._plan.preview, self.window.layers.colors)
            return
        if self._pivot is None:
            if not self.editable():
                return
            if self._candidates is None:
                self._candidates = self.document.results.all_points()
            px, py, _ = self.snap(x, y, self._candidates, modifiers)
            self._plan = self.document.moves.plan_drag(self.window.selection)
            if not self._plan.roots:
                return
            self._pivot = (px, py)
            self.canvas.show_drag_preview(self._plan.preview, self.window.layers.colors)
            self.window.update_overlay()
            self.window.prompt(self.hint())
            return
        angle, pivot, roots = self._angle_at(x, y, modifiers), self._pivot, self._plan.roots
        self.cancel()
        if angle:
            self.window.run(lambda: self.document.moves.rotate(roots, angle, pivot))
        self.window.prompt(self.hint())

    def _angle_at(self, x, y, modifiers) -> float:
        px, py = self._pivot
        if math.hypot(x - px, y - py) * self.canvas.pixels_per_um() < DRAG_THRESHOLD_PX:
            return 0.0
        angle = math.degrees(math.atan2(y - py, x - px))
        if self._ring is not None:  # relative to where the ring was grabbed
            angle = (angle - self._ring + 180) % 360 - 180
        if not modifiers & CTRL:
            angle = round(angle / self.step) * self.step
        return round(angle, 6)

    def move(self, x, y, modifiers, left) -> None:
        if self._pivot is None:
            return
        self._angle = self._angle_at(x, y, modifiers)
        self.canvas.rotate_drag_preview(self._angle, self._pivot)
        if self._ring is not None:
            self.canvas.set_gizmo_sweep(self._ring, self._angle)
        finish = "release" if self._ring is not None else "click"
        self.window.prompt(f"Rotate by {self._angle:g}°   ({finish} to apply, Esc cancels)")

    def release(self, x, y, modifiers) -> None:
        if self._ring is None:
            return
        angle, pivot, roots = self._angle_at(x, y, modifiers), self._pivot, self._plan.roots
        self.cancel()
        if angle:
            self.window.run(lambda: self.document.moves.rotate(roots, angle, pivot))
        self.window.prompt(self.hint())

    def cancel(self) -> bool:
        if self._ring is not None:
            self.canvas.set_gizmo_active(None)
        return super().cancel()

    def markers(self) -> dict[str, list[Candidate]]:
        return {"anchor": [("pivot", *self._pivot)]} if self._pivot else {}


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

    @property
    def busy(self) -> bool:
        return self.step is not None

    def activate(self) -> None:
        super().activate()
        if len(self.window.selection) == 1:
            self.begin(self.window.selection[0])

    def hint(self) -> str:
        name = self.document.node(self.path).name if self.path else None
        match self.step:
            case "own":
                return f"Align {name}: click the point of {name} to align (Esc cancels)"
            case "target":
                return f"Now click the point to put {self.point} on (Esc cancels)"
        return "Align: click the shape to align"

    def begin(self, path: NodePath) -> None:
        if not self.editable():
            return
        candidates = self.document.results.node_points(path)
        if not candidates:
            self.window.report_error("this shape has no points to align (does it have geometry?)")
            return
        self.step, self.path, self.candidates = "own", path, candidates
        self.window.prompt(self.hint())
        self.window.update_overlay()

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
        if self.step == "own":
            targets = [
                (n, tx, ty) for n, _, tx, ty in self.document.results.align_targets(self.path)
            ]
            if not targets:
                self.reset()
                self.window.report_error("there is no other named shape here to align to")
                return
            self.step, self.point, self.candidates = "target", picked, targets
            self.window.prompt(self.hint())
            self.window.update_overlay()
            return
        path, point = self.path, self.point
        self.reset()
        self.window.update_overlay()
        ok, _ = self.window.run(
            lambda: self.document.nodes.set_align(path, Align(point=point, to=picked))
        )
        if ok:
            self.window.statusBar().showMessage(f"Aligned {point} to {picked}", 5000)

    def _nearest(self, x, y) -> str | None:
        label = self.snap(x, y, self.candidates, NONE, grid=False)[2]
        return label

    def hover_label(self, x, y) -> str | None:
        return self._nearest(x, y) if self.step else None

    def markers(self) -> dict[str, list[Candidate]]:
        return {"pick": self.candidates} if self.step else {}

    def cancel(self) -> bool:
        busy = super().cancel()
        if busy:
            self.window.update_overlay()
        return busy


class CornersTool(Tool):
    """Round corners: pick a shape, then click its corners.

    A click rounds a corner (with the last radius used) or, on a rounded one,
    makes it sharp again; dragging away from a corner sets its radius, drawn
    as you drag. The corners go into the shape's corners modifier, recorded
    so that they follow the design (see :mod:`mems_sketch.editing.corners`).
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
        self._press: tuple[float, float] | None = None  # the corner a press is on
        self._dragged: float | None = None  # the radius being dragged

    @property
    def busy(self) -> bool:
        return self.path is not None

    def activate(self) -> None:
        super().activate()
        if len(self.window.selection) == 1:
            self.begin(self.window.selection[0])

    def design_changed(self) -> None:
        """Stay on the shape (its corners may have moved or gone), unless it is gone."""
        self._press = self._dragged = None
        if self.path is not None and not self._refresh():
            self.reset()

    def hint(self) -> str:
        if self.path is None:
            return "Corners: click the shape whose corners to round"
        name = self.document.node(self.path).name or self.document.node(self.path).kind
        return (
            f"Corners of {name}: click a corner to round it (again: sharp), drag from it "
            "to set the radius; Esc when done"
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
            self.rounded = self.document.corners.rounded(self.path)
        except (ValueError, KeyError, IndexError):  # e.g. undone away
            return False
        return True

    def _corner_at(self, x: float, y: float) -> tuple[float, float] | None:
        points = self.candidates + [p for _, p in self.rounded]
        best, found = self.reach, None
        for px, py in points:
            distance = math.hypot(px - x, py - y)
            if distance < best:
                best, found = distance, (px, py)
        return found

    def press(self, x, y, modifiers) -> None:
        corner = self._corner_at(x, y) if self.path is not None else None
        if corner is None:
            hit = self.window.hit(x, y)
            self.window.select_click(hit, False)
            if hit is not None:
                self.begin(hit)
            return
        self._press, self._dragged = corner, None

    def move(self, x, y, modifiers, left) -> None:
        if self._press is None or not left:
            return
        distance = math.hypot(x - self._press[0], y - self._press[1])
        if self._dragged is None and distance * self.canvas.pixels_per_um() < self.DRAG_PX:
            return
        radius = distance if modifiers & CTRL else max(round(distance, 1), 0.1)
        self._dragged = radius
        self.window.prompt(f"Radius {radius:g} µm (release to set; Ctrl: exact)")
        with contextlib.suppress(ValueError, KeyError):
            node, _ = self.document.corners.with_corner(self.path, *self._press, _um(radius))
            self.window._preview_node(node, self.path)

    def release(self, x, y, modifiers) -> None:
        corner, radius = self._press, self._dragged
        self._press = self._dragged = None
        if corner is None:
            return
        path = self.path
        existing = self.document.corners.at(path, *corner)
        if radius is not None:
            self.radius = _um(radius)
            self.window.run(lambda: self.document.corners.add(path, *corner, self.radius))
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


class MeasureTool(Tool):
    """Click two points to measure; the rulers stay (per component) until cleared."""

    name, label, shortcut = "measure", "Measure", "D"
    edits = False
    cursor = Qt.CursorShape.CrossCursor
    icon = "measure"

    def reset(self) -> None:
        self._start: tuple[float, float] | None = None
        self._end: tuple[float, float] | None = None
        self._snap: Candidate | None = None
        self._candidates: list[Candidate] | None = None

    @property
    def busy(self) -> bool:
        return self._start is not None

    def hint(self) -> str:
        if self._start is None:
            return "Measure: click the first point (snaps to shape points; Ctrl: no snapping)"
        return "Measure: click the second point (Esc cancels)"

    def _points(self) -> list[Candidate]:
        if self._candidates is None:
            self._candidates = self.document.results.all_points()
        return self._candidates

    def press(self, x, y, modifiers) -> None:
        px, py, _ = self.snap(x, y, self._points(), modifiers)
        if self._start is None:
            self._start = (px, py)
            self.window.prompt(self.hint())
            return
        ruler = (*self._start, px, py)
        self.reset()
        self.window.add_ruler(ruler)
        self.window.prompt(self.hint())

    def move(self, x, y, modifiers, left) -> None:
        px, py, label = self.snap(x, y, self._points(), modifiers)
        self._snap = (label, px, py) if label else None
        self.canvas.show_points("snap", [self._snap] if self._snap else [])
        if self._start is not None:
            self._end = (px, py)
            self.window.draw_rulers(extra=(*self._start, px, py))
            dx, dy = px - self._start[0], py - self._start[1]
            self.window.prompt(f"Distance {math.hypot(dx, dy):.3f} µm  (dx {dx:.3f}, dy {dy:.3f})")

    def hover_label(self, x, y) -> str | None:
        return self.snap(x, y, self._points(), NONE, grid=False)[2]

    def markers(self) -> dict[str, list[Candidate]]:
        return {"anchor": [("start", *self._start)]} if self._start else {}

    def cancel(self) -> bool:
        busy = super().cancel()
        self.canvas.show_points("snap", [])
        self.window.draw_rulers()
        return busy


class AngleTool(Tool):
    """Click the vertex, then a point on each arm; the angle stays like a ruler."""

    name, label, shortcut = "angle", "Measure angle", "N"
    edits = False
    cursor = Qt.CursorShape.CrossCursor
    icon = "angle"

    def reset(self) -> None:
        self._placed: list[tuple[float, float]] = []  # the vertex, then the first arm
        self._candidates: list[Candidate] | None = None

    @property
    def busy(self) -> bool:
        return bool(self._placed)

    def hint(self) -> str:
        if not self._placed:
            return "Angle: click the vertex (snaps to shape points; Ctrl: no snapping)"
        if len(self._placed) == 1:
            return "Angle: click a point on the first arm (Esc cancels)"
        return "Angle: click a point on the second arm (Shift: 15° steps, Esc cancels)"

    def _points(self) -> list[Candidate]:
        if self._candidates is None:
            self._candidates = self.document.results.all_points()
        return self._candidates

    def _locate(self, x, y, modifiers) -> tuple[float, float, str | None]:
        px, py, label = self.snap(x, y, self._points(), modifiers)
        if len(self._placed) == 2 and modifiers & SHIFT:  # the second arm in 15° steps
            (vx, vy), (ax, ay) = self._placed
            first = math.atan2(ay - vy, ax - vx)
            turn = math.atan2(py - vy, px - vx) - first
            turn = math.radians(round(math.degrees(turn) / 15) * 15)
            reach = math.hypot(px - vx, py - vy)
            px, py = vx + reach * math.cos(first + turn), vy + reach * math.sin(first + turn)
            label = None
        return px, py, label

    def press(self, x, y, modifiers) -> None:
        px, py, _ = self._locate(x, y, modifiers)
        if len(self._placed) < 2:
            if self._placed and (px, py) == self._placed[0]:
                return  # an arm needs a point other than the vertex
            self._placed.append((px, py))
            self.window.prompt(self.hint())
            return
        if (px, py) == self._placed[0]:
            return
        ruler = (*self._placed[0], *self._placed[1], px, py)
        self.reset()
        self.window.add_ruler(ruler)
        self.window.prompt(self.hint())

    def move(self, x, y, modifiers, left) -> None:
        px, py, label = self._locate(x, y, modifiers)
        self.canvas.show_points("snap", [(label, px, py)] if label else [])
        if len(self._placed) == 1:
            self.window.draw_rulers(extra=(*self._placed[0], px, py))
        elif len(self._placed) == 2:
            vertex, first = self._placed
            self.window.draw_rulers(extra=(*vertex, *first, px, py))
            sweep = angle_between(vertex, first, (px, py))[1]
            self.window.prompt(f"Angle {sweep:.2f}°")

    def hover_label(self, x, y) -> str | None:
        return self.snap(x, y, self._points(), NONE, grid=False)[2]

    def markers(self) -> dict[str, list[Candidate]]:
        names = ("vertex", "arm")
        return {"anchor": [(n, *p) for n, p in zip(names, self._placed, strict=False)]}

    def cancel(self) -> bool:
        busy = super().cancel()
        self.canvas.show_points("snap", [])
        self.window.draw_rulers()
        return busy


class DrawTool(Tool):
    """Base of the drawing tools: points snap to shape points, else to the grid."""

    draws = True
    uses_layer: ClassVar[bool] = True  # draws on the chosen layer (a guide does not)
    cursor = Qt.CursorShape.CrossCursor
    noun: ClassVar[str]

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
            geometry = Evaluator(_no_components).render_shape(shape, {})
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
            self.window.prompt(f"{self.describe(shape)}   (click to finish, Esc cancels)")

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


TOOLS: tuple[type[Tool], ...] = (
    SelectTool,
    HandTool,
    MoveTool,
    RotateTool,
    AlignTool,
    CornersTool,
    MeasureTool,
    AngleTool,
    RectTool,
    CircleTool,
    PolygonTool,
    PathTool,
    GuideTool,
)


def _um(value: float) -> float:
    """A coordinate without floating-point noise (grid steps like 0.1 add up badly)."""
    return round(value, 6) + 0.0  # + 0.0 turns -0.0 into 0.0


def _no_components(name: str):
    raise KeyError(name)  # drawn primitives never refer to components


def probe(x: float, y: float, reach: float = 0.0) -> kdb.Region:
    """A small square around a point, to test what lies under it (``reach`` in µm)."""
    point = kdb.Point(to_dbu(x), to_dbu(y))
    r = max(1, to_dbu(reach))
    return kdb.Region(kdb.Box(point.x - r, point.y - r, point.x + r, point.y + r))
