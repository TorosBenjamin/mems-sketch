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

A tool stays active until another one is chosen; Esc cancels what the tool
is doing, and pressed again goes back to Select. Tools that change the design
are refused on read-only tabs.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, ClassVar

import klayout.db as kdb
from PySide6.QtCore import Qt

from mems_sketch.core.component import to_dbu
from mems_sketch.core.shapes import Align, NodePath

if TYPE_CHECKING:
    from mems_sketch.gui.app import MainWindow
    from mems_sketch.gui.document import DragPlan

SNAP_PX = 10  # a point snaps to another within this many pixels
DRAG_THRESHOLD_PX = 4  # a press moving less than this is a click, not a drag
ANGLE_STEP = 15.0  # degrees the Rotate tool snaps to
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
    cursor: ClassVar[Qt.CursorShape] = Qt.CursorShape.ArrowCursor

    def __init__(self, window: MainWindow) -> None:
        self.window = window

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

    def hint(self) -> str:
        return ""

    # -- events (x, y in µm, in the current tab's component) ----------------

    def press(self, x: float, y: float, modifiers) -> None:
        pass

    def move(self, x: float, y: float, modifiers, left: bool) -> None:
        pass

    def release(self, x: float, y: float, modifiers) -> None:
        pass

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

    def snap(
        self, x: float, y: float, candidates: list[Candidate], modifiers, grid: bool = True
    ) -> tuple[float, float, str | None]:
        """``(x, y)`` snapped to the nearest candidate point in reach, else to the grid.

        Ctrl turns snapping off. Returns the point and the candidate's label.
        """
        if modifiers & CTRL:
            return x, y, None
        best, found = SNAP_PX / self.canvas.pixels_per_um(), None
        for label, px, py in candidates:
            distance = math.hypot(px - x, py - y)
            if distance < best:
                best, found = distance, (label, px, py)
        if found is not None:
            return found[1], found[2], found[0]
        if grid:
            step = self.canvas.grid_step()
            return round(x / step) * step, round(y / step) * step, None
        return x, y, None


class SelectTool(Tool):
    name, label, shortcut = "select", "Select", "V"
    edits = False

    def reset(self) -> None:
        self._press: tuple[float, float] | None = None
        self._press_hit: NodePath | None = None
        self._pending: NodePath | None = None
        self._mode: str | None = None  # None, "move", "box" or "blocked"
        self._plan: DragPlan | None = None
        self._snap = None

    @property
    def busy(self) -> bool:
        return self._mode in ("move", "box")

    def hint(self) -> str:
        return "Click to select, drag to move, drag on empty space to select with a box"

    def press(self, x, y, modifiers) -> None:
        additive = bool(modifiers & (CTRL | SHIFT))
        hit = self.window.hit(x, y)
        self.reset()
        self._press, self._press_hit = (x, y), hit
        if hit is not None and not additive and self.window.on_selection(x, y):
            self._pending = hit  # keep the selection: this may become a drag
            return
        self.window.select_click(hit, additive)

    def move(self, x, y, modifiers, left) -> None:
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
        self._plan = self.document.drag_plan(self.window.selection)
        if not self._plan.roots:
            self._mode = "blocked"
            return
        self._mode = "move"
        self.canvas.show_drag_preview(self._plan.preview, self.window.layers.colors)

    def _snapped_move(self, plan, dx, dy, modifiers):
        """The move, snapped: a moving point onto another shape's point, else the grid."""
        if not modifiers & CTRL:
            best, found = SNAP_PX / self.canvas.pixels_per_um(), None
            for path, name, px, py in plan.points:
                for target, tx, ty in plan.targets:
                    distance = math.hypot(px + dx - tx, py + dy - ty)
                    if distance < best:
                        best, found = distance, (path, name, target, tx, ty, px, py)
            if found is not None:
                path, name, target, tx, ty, px, py = found
                return tx - px, ty - py, (path, name, target, tx, ty)
        if modifiers & CTRL:
            return dx, dy, None
        step = self.canvas.grid_step()
        return round(dx / step) * step, round(dy / step) * step, None

    def _drag_to(self, dx, dy, modifiers) -> None:
        dx, dy, self._snap = self._snapped_move(self._plan, dx, dy, modifiers)
        self.canvas.move_drag_preview(dx, dy)
        snap = self._snap
        self.canvas.show_points("snap", [(snap[2], snap[3], snap[4])] if snap else [])
        text = f"Move Δx {dx:g} µm, Δy {dy:g} µm"
        if snap:
            text += f"   {snap[1]} on {snap[2]} (release with Shift to align it there)"
        self.window.prompt(text)

    def release(self, x, y, modifiers) -> None:
        mode, press, pending, plan = self._mode, self._press, self._pending, self._plan
        self.cancel()
        self.canvas.show_points("snap", [])
        if mode == "move":
            dx, dy, snap = self._snapped_move(plan, x - press[0], y - press[1], modifiers)
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
            ok, _ = window.run(lambda: self.document.set_align(path, Align(point=point, to=target)))
            if ok:
                window.statusBar().showMessage(f"Aligned {point} to {target}", 5000)
            return
        detach = bool(modifiers & ALT)
        window.run(lambda: self.document.move(plan.roots, dx, dy, detach=detach))

    def cancel(self) -> bool:
        busy = super().cancel()
        self.canvas.show_points("snap", [])
        return busy


class HandTool(Tool):
    name, label, shortcut = "hand", "Hand", "H"
    edits = False
    cursor = Qt.CursorShape.OpenHandCursor

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

    def reset(self) -> None:
        self._base: tuple[float, float, str | None] | None = None
        self._plan: DragPlan | None = None
        self._snap: Candidate | None = None
        self._candidates: list[Candidate] | None = None

    @property
    def busy(self) -> bool:
        return self._base is not None

    def hint(self) -> str:
        if not self.window.selection:
            return "Move: click a shape to move"
        if self._base is None:
            return "Move: click the base point (snaps to shape points; Ctrl: no snapping)"
        return "Move: click where the base point goes (Esc cancels)"

    def _points(self) -> list[Candidate]:
        if self._candidates is None:
            self._candidates = self.document.all_points()
        return self._candidates

    def press(self, x, y, modifiers) -> None:
        if not self.window.selection:  # nothing to move yet: this click selects
            self.window.select_click(self.window.hit(x, y), False)
            self.window.prompt(self.hint())
            return
        if self._base is None:
            if not self.editable():
                return
            bx, by, label = self.snap(x, y, self._points(), modifiers)
            self._plan = self.document.drag_plan(self.window.selection)
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
        self.window.run(lambda: self.document.move(roots, dx, dy))
        self.window.prompt(self.hint())

    def _destination(self, x, y, modifiers) -> tuple[float, float]:
        tx, ty, label = self.snap(x, y, self._plan.targets, modifiers)
        self._snap = (label, tx, ty) if label else None
        return tx - self._base[0], ty - self._base[1]

    def move(self, x, y, modifiers, left) -> None:
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

    def markers(self) -> dict[str, list[Candidate]]:
        return {"anchor": [("base", self._base[0], self._base[1])]} if self._base else {}

    def cancel(self) -> bool:
        busy = super().cancel()
        self.canvas.show_points("snap", [])
        return busy


class RotateTool(Tool):
    """Rotate the selection about a pivot; the angle snaps to 15° steps."""

    name, label, shortcut = "rotate", "Rotate", "R"
    cursor = Qt.CursorShape.CrossCursor

    def reset(self) -> None:
        self._pivot: tuple[float, float] | None = None
        self._plan: DragPlan | None = None
        self._angle = 0.0
        self._candidates: list[Candidate] | None = None

    @property
    def busy(self) -> bool:
        return self._pivot is not None

    def hint(self) -> str:
        if not self.window.selection:
            return "Rotate: click a shape to rotate"
        if self._pivot is None:
            return "Rotate: click the pivot (snaps to shape points)"
        return "Rotate: move to set the angle, click to apply (Ctrl: free angle, Esc cancels)"

    def press(self, x, y, modifiers) -> None:
        if not self.window.selection:
            self.window.select_click(self.window.hit(x, y), False)
            self.window.prompt(self.hint())
            return
        if self._pivot is None:
            if not self.editable():
                return
            if self._candidates is None:
                self._candidates = self.document.all_points()
            px, py, _ = self.snap(x, y, self._candidates, modifiers)
            self._plan = self.document.drag_plan(self.window.selection)
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
            self.window.run(lambda: self.document.rotate(roots, angle, pivot))
        self.window.prompt(self.hint())

    def _angle_at(self, x, y, modifiers) -> float:
        px, py = self._pivot
        if math.hypot(x - px, y - py) * self.canvas.pixels_per_um() < DRAG_THRESHOLD_PX:
            return 0.0
        angle = math.degrees(math.atan2(y - py, x - px))
        if not modifiers & CTRL:
            angle = round(angle / ANGLE_STEP) * ANGLE_STEP
        return round(angle, 6)

    def move(self, x, y, modifiers, left) -> None:
        if self._pivot is None:
            return
        self._angle = self._angle_at(x, y, modifiers)
        self.canvas.rotate_drag_preview(self._angle, self._pivot)
        self.window.prompt(f"Rotate by {self._angle:g}°   (click to apply, Esc cancels)")

    def markers(self) -> dict[str, list[Candidate]]:
        return {"anchor": [("pivot", *self._pivot)]} if self._pivot else {}


class AlignTool(Tool):
    """Pick a shape, one of its points, then the point of another shape to put it on."""

    name, label, shortcut = "align", "Align", "A"
    cursor = Qt.CursorShape.CrossCursor

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
        candidates = self.document.node_points(path)
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
            targets = [(n, tx, ty) for n, _, tx, ty in self.document.align_targets(self.path)]
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
            lambda: self.document.set_align(path, Align(point=point, to=picked))
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


class MeasureTool(Tool):
    """Click two points to measure; the rulers stay (per component) until cleared."""

    name, label, shortcut = "measure", "Measure", "D"
    edits = False
    cursor = Qt.CursorShape.CrossCursor

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
            self._candidates = self.document.all_points()
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


TOOLS: tuple[type[Tool], ...] = (
    SelectTool,
    HandTool,
    MoveTool,
    RotateTool,
    AlignTool,
    MeasureTool,
)


def probe(x: float, y: float) -> kdb.Region:
    """A tiny region at a point, to test what lies under it."""
    point = kdb.Point(to_dbu(x), to_dbu(y))
    return kdb.Region(kdb.Box(point.x - 1, point.y - 1, point.x + 1, point.y + 1))
