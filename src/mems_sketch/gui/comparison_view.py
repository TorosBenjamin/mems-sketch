"""The comparison tab: two versions of a component on the canvas.

Opened from the Commit panel (the changes since the last commit) or the Git
panel (what a commit changed, or a commit against the design now). The canvas
is the main view: the new version with the material added tinted and the
material removed hatched (*Overlay*), or the two versions next to each other
(*Side by side*), zoomed and panned together.

Each shape that was edited gets a filled mark, numbered; a shape that only
moved or changed size because of another change (a parameter it follows, a
shape it is aligned to) gets a ring. Hover a mark for what changed; step
through them all with the arrows, which zooms to each, so the changes too
small to see at a glance are found as well. For the uncommitted changes a
shape can be rolled back from its card (one undoable edit). The tab follows
edits while it compares with the design now.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.core.component import Geometry
from mems_sketch.core.diff import ADDED, REMOVED, Change, geometry_changes
from mems_sketch.core.shapes import NodePath, frame_of
from mems_sketch.editing import EditSession
from mems_sketch.editing.history import Comparison, grouped
from mems_sketch.engine import Build
from mems_sketch.gui import icons, theme, widgets
from mems_sketch.gui.canvas import MARK_RADIUS_PX, ChangeMark, LayoutCanvas

UNCOMMITTED = ("uncommitted",)
MOVED = "moved"  # a mark's status besides ADDED, REMOVED and "changed"
MOVE_TOLERANCE_UM = 1e-6  # a box that moved less than this did not move
CARD_REACH_PX = 40  # the card stays while the cursor is this close to its mark
BOX = tuple[float, float, float, float]

Key = tuple[str, ...]


@dataclass
class Mark:
    """A changed shape on the canvas: its box (µm, the component's frame), what
    happened (``ADDED``, ``REMOVED``, ``"changed"`` or ``MOVED``) and the card's
    text. ``change`` is the change listed for it (None for a shape that moved)."""

    name: str
    status: str
    box: BOX
    lines: list[str] = field(default_factory=list)
    change: Change | None = None

    @property
    def edited(self) -> bool:
        return self.status != MOVED


def find_marks(
    changes: list[Change], component: str, old: Build | None, new: Build | None
) -> list[Mark]:
    """The marks for ``component``: the shapes its changes name, then the top-level
    shapes that came out elsewhere without being edited."""
    new_records = new.records() if new is not None else {}
    old_records = old.records() if old is not None else {}
    new_names = _by_name(new_records)
    old_names = _by_name(old_records)
    marks: list[Mark] = []
    edited: set[str] = set()
    edited_paths: list[NodePath] = []
    for change in changes:
        if change.component != component or change.what != "shape":
            continue
        name = change.subject.rsplit(" › ", 1)[-1]
        edited.add(name)
        if change.action == REMOVED:
            path = old_names.get(name)
            box = _box(old_records, path) if path is not None else None
        else:
            path = change.path
            box = _box(new_records, path) if path in new_records else None
            if path is not None:
                edited_paths.append(path)
        if box is None:
            continue
        lines = [_plain(d) for d in change.details] or [change.action]
        marks.append(Mark(name, change.action, box, lines, change))
    for name, path in new_names.items():
        if len(path) != 1 or name in edited or name not in old_names:
            continue
        if any(other[:1] == path for other in edited_paths):  # it holds an edited shape
            continue
        before, after = _box(old_records, old_names[name]), _box(new_records, path)
        if before is None or after is None:
            continue
        lines = _moved(before, after)
        if lines:
            marks.append(Mark(name, MOVED, after, [*lines, "because of another change"]))
    return marks


def _by_name(records) -> dict[str, NodePath]:
    found: dict[str, NodePath] = {}
    for path, record in records.items():
        name = record.points.name
        if name and name not in found:
            found[name] = path
    return found


def _box(records, path: NodePath) -> BOX | None:
    """A node's box in the component's frame."""
    record = records.get(path)
    box = record.geometry.bbox() if record is not None else None
    if box is None:
        return None
    frame, (x0, y0, x1, y1) = frame_of(records, path), box
    corners = [frame.apply(x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    xs, ys = [x for x, _ in corners], [y for _, y in corners]
    return min(xs), min(ys), max(xs), max(ys)


def _moved(before: BOX, after: BOX) -> list[str]:
    """How a box moved or changed size, in words (none if it did not)."""
    (a0, b0, a1, b1), (c0, d0, c1, d1) = before, after
    lines = []
    dx, dy = (c0 + c1 - a0 - a1) / 2, (d0 + d1 - b0 - b1) / 2
    if abs(dx) > MOVE_TOLERANCE_UM or abs(dy) > MOVE_TOLERANCE_UM:
        parts = [f"{axis} {_signed(d)} µm" for axis, d in (("x", dx), ("y", dy)) if abs(d) > 1e-9]
        lines.append("moved " + ", ".join(parts))
    w0, h0, w1, h1 = a1 - a0, b1 - b0, c1 - c0, d1 - d0
    if abs(w1 - w0) > MOVE_TOLERANCE_UM or abs(h1 - h0) > MOVE_TOLERANCE_UM:
        lines.append(f"now {_num(w1)} × {_num(h1)} µm (was {_num(w0)} × {_num(h0)})")
    return lines


def _signed(value: float) -> str:
    return ("+" if value > 0 else "−") + _num(abs(value))


def _num(value: float) -> str:
    return f"{value:.4g}"


def _plain(detail: str) -> str:
    return detail.removeprefix("params.")


class ComparisonView(QWidget):
    """See the module docstring. The window shows it as a tab of its own."""

    error = Signal(str)

    def __init__(self, document: EditSession) -> None:
        super().__init__()
        self.document = document
        self.component = ""  # the component compared
        self.key: Key = UNCOMMITTED
        self.comparison: Comparison | None = None
        self.marks: list[Mark] = []
        self.current = -1  # the mark stepped to
        self._colors: dict = {}
        self._visible: dict = {}
        self._fitted = False
        self._syncing = False
        self._card_mark: Mark | None = None

        self.what = QLabel()  # what is compared with what
        self.what.setObjectName("muted")
        self.component_box = QComboBox()
        self.component_box.setToolTip("The component compared")
        self.component_box.activated.connect(
            lambda _index: self.set_component(self.component_box.currentText())
        )
        self.overlay_button = self._mode_button(
            "Overlay", "Both versions in one, the changes marked"
        )
        self.side_button = self._mode_button("Side by side", "The earlier version on the left")
        modes = QButtonGroup(self)
        for button in (self.overlay_button, self.side_button):
            modes.addButton(button)
        self.overlay_button.setChecked(True)
        modes.buttonToggled.connect(lambda _b, _on: self._mode_changed())
        self.legend = QLabel()
        self.legend.setObjectName("muted")
        self.previous_button = self._step_button("up", "Previous change", -1)
        self.next_button = self._step_button("down", "Next change", 1)
        self.counter = QLabel()  # "2 of 5"
        self.counter.setObjectName("muted")

        bar = QWidget()
        bar.setObjectName("comparison-bar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(bar)
        space = theme.metric("space_m")
        row.setContentsMargins(space, theme.metric("space_xs"), space, theme.metric("space_xs"))
        row.setSpacing(theme.metric("space_s"))
        row.addWidget(self.component_box)
        row.addWidget(self.what, 1)
        row.addWidget(self.overlay_button)
        row.addWidget(self.side_button)
        row.addSpacing(space)
        row.addWidget(self.legend)
        row.addSpacing(space)
        row.addWidget(self.counter)
        row.addWidget(self.previous_button)
        row.addWidget(self.next_button)

        self.old_canvas = LayoutCanvas()
        self.canvas = LayoutCanvas()
        for canvas in (self.old_canvas, self.canvas):
            canvas.mode_palette.hide()
            canvas.view_changed.connect(lambda c=canvas: self._sync(c))
            canvas.cursor_moved.connect(lambda _x, _y, c=canvas: self._hovered(c))
            canvas.viewport().installEventFilter(self)
        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.setObjectName("comparison-split")
        self.split.setChildrenCollapsible(False)
        self.split.addWidget(self.old_canvas)
        self.split.addWidget(self.canvas)
        self.old_canvas.hide()
        self.problem = widgets.EmptyState()  # a version that does not build, in place of it
        self.problem.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(bar)
        layout.addWidget(self.split, 1)
        layout.addWidget(self.problem, 1)

        self.card = _Card(self)
        self.card.show_old.connect(self.show_old)
        self.card.roll_back.connect(self.roll_back)
        self.card.hide()
        self._later = QTimer(self)  # edits come in bursts: compare once they settle
        self._later.setSingleShot(True)
        self._later.setInterval(0)
        self._later.timeout.connect(self.refresh)

    @staticmethod
    def _mode_button(text: str, tip: str) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tip)
        button.setCheckable(True)
        button.setAutoRaise(True)
        return button

    def _step_button(self, icon: str, tip: str, step: int) -> QToolButton:
        button = QToolButton()
        icons.bind(button, icon)
        button.setToolTip(f"{tip} (zooms to it)")
        button.setAutoRaise(True)
        button.clicked.connect(lambda: self.step(step))
        return button

    # -- the tab ---------------------------------------------------------------------

    def title(self) -> str:
        return f"{self.component}: changes" if self.component else "Changes"

    def icon_name(self) -> str:
        return "history"

    def tooltip(self) -> str:
        return f"{self.component}: {self.what.text()}" if self.component else "Changes"

    def canvases(self) -> list[LayoutCanvas]:
        return [self.old_canvas, self.canvas]

    # -- what is compared -----------------------------------------------------------

    def compare(
        self, key: Key = UNCOMMITTED, component: str | None = None, change: Change | None = None
    ) -> None:
        """Compare ``key``: ``UNCOMMITTED``, ``("commit", sha)`` (what it changed) or
        ``("since", sha)`` (it against the design now), for ``component`` (the
        first one changed if not given); ``change`` is stepped to."""
        self.key = key
        self.comparison = self._comparison()
        if component is None and change is not None:
            component = change.component
        changed = self._changed_components()
        if component is None:
            active = self.document.active
            component = active if active in changed or not changed else changed[0]
        self.component = component
        self._fitted = False
        self.refresh(recompare=False)
        if change is not None:
            for index, mark in enumerate(self.marks):
                if mark.change == change:
                    self.go_to(index)
                    break

    def set_component(self, component: str) -> None:
        if component and component != self.component:
            self.component = component
            self._fitted = False
            self.refresh(recompare=False)

    def follow(self) -> None:
        """After an edit: a comparison with the design now is worked out again."""
        if self.key == UNCOMMITTED or self.key[0] == "since":
            self._later.start()

    def set_palette(self, colors: dict, visible: dict) -> None:
        self._colors, self._visible = colors, visible

    def _comparison(self) -> Comparison | None:
        history = self.document.history
        if not history.available:
            return None
        try:
            if self.key == UNCOMMITTED:
                return history.uncommitted()
            if self.key[0] == "since":
                return history.compare(self.key[1], None)
            return history.commit(self.key[1])
        except Exception as exc:  # noqa: BLE001 - e.g. an old version that no longer loads
            self.error.emit(f"Cannot read that version: {exc}")
            return None

    def _changed_components(self) -> list[str]:
        if self.comparison is None:
            return []
        return [g.key for g in grouped(self.comparison.changes) if g.component]

    def _describe(self) -> str:
        history = self.document.history
        subjects = {c.sha: c.subject for c in history.commits()}

        def name(sha: str | None) -> str:
            if sha is None:
                return "nothing"
            subject = subjects.get(sha, "")
            return f"{sha[:7]} · {subject}" if subject else sha[:7]

        comparison = self.comparison
        if comparison is None:
            return ""
        if comparison.new is None:
            now = "now (not committed)" if self.key == UNCOMMITTED else "now"
            return f"{name(comparison.old)}  →  {now}"
        return f"{name(comparison.old)}  →  {name(comparison.new)}"

    # -- drawing ------------------------------------------------------------------------

    def refresh(self, recompare: bool = True) -> None:
        """Work out the comparison again (``recompare``) and draw it."""
        if recompare:
            self.comparison = self._comparison()
        self.what.setText(self._describe())
        self._fill_components()
        comparison = self.comparison
        old = new = None
        problem = ""
        if comparison is not None:
            try:
                old, new = self.document.history.builds(comparison, self.component)
            except ValueError as exc:
                problem = str(exc)
        else:
            problem = "The project is not in a git repository."
        self.problem.set_text(problem)
        self.problem.setVisible(bool(problem))
        self.split.setVisible(not problem)
        old_geometry = old.geometry if old is not None else None
        new_geometry = new.geometry if new is not None else None
        added, removed = geometry_changes(old_geometry, new_geometry)
        self.canvas.show_geometry(new_geometry or Geometry(), self._colors, self._visible, [])
        self.old_canvas.show_geometry(old_geometry or Geometry(), self._colors, self._visible, [])
        side = self.side_button.isChecked()
        self.old_canvas.set_caption("Before", self.component)
        self.canvas.set_caption("After" if side else self.component, "" if side else "changes")
        self.canvas.show_changes(added, None if side else removed)
        self.old_canvas.show_changes(None, removed)
        changes = comparison.changes if comparison is not None else []
        self.marks = find_marks(changes, self.component, old, new) if not problem else []
        self.current = min(self.current, len(self.marks) - 1)
        self._update_legend()
        self._draw_marks()
        if not self._fitted and not problem:
            self._fitted = True
            target = self.canvas if (new_geometry and not new_geometry.is_empty()) else None
            (target or self.old_canvas).fit()
            if target is None:
                self.canvas.set_view_state(*self.old_canvas.view_state())
        if self._card_mark is not None and self._card_mark not in self.marks:
            self.card.hide()
            self._card_mark = None

    def _fill_components(self) -> None:
        names = self._changed_components()
        if self.component and self.component not in names:
            names.append(self.component)
        box = self.component_box
        box.blockSignals(True)
        box.clear()
        box.addItems(names)
        box.setCurrentText(self.component)
        box.blockSignals(False)

    def _update_legend(self) -> None:
        canvas = self.canvas.theme  # the canvas draws the material in these
        changed = theme.color("vcs_changed").name()

        def entry(color: str, sign: str, text: str) -> str:
            return f"<span style='color:{color}'>{sign}</span> {text}"

        self.legend.setText(
            "&nbsp;&nbsp;".join(
                (
                    entry(canvas["added"], "■", "added"),
                    entry(canvas["removed"], "▨", "removed"),
                    entry(changed, "●", "edited"),
                    entry(changed, "○", "moved"),
                )
            )
        )
        count = len(self.marks)
        if count and self.current >= 0:
            self.counter.setText(f"{self.current + 1} of {count}")
        elif count:
            self.counter.setText(f"{count} change{'s' if count != 1 else ''}")
        else:
            self.counter.setText("no changes drawn")
        for button in (self.previous_button, self.next_button):
            button.setEnabled(bool(count))

    def _draw_marks(self) -> None:
        number = 0
        drawn = []
        for index, mark in enumerate(self.marks):
            if mark.edited:
                number += 1
            color = self._mark_color(mark)
            drawn.append(
                ChangeMark(
                    mark.box[2],
                    mark.box[3],
                    str(number) if mark.edited else "",
                    mark.edited,
                    color,
                    index == self.current,
                )
            )
        side = self.side_button.isChecked()
        self.canvas.show_marks(
            [d for d, m in zip(drawn, self.marks, strict=True) if m.status != REMOVED or not side]
        )
        self.old_canvas.show_marks(
            [d for d, m in zip(drawn, self.marks, strict=True) if m.status == REMOVED]
            if side
            else []
        )

    def _mark_color(self, mark: Mark) -> str:
        """Added and removed shapes in the canvas's colours for them; the others in
        the colour of a change."""
        if mark.status in (ADDED, REMOVED):
            return self.canvas.theme["added" if mark.status == ADDED else "removed"]
        return theme.color("vcs_changed").name()

    # -- modes and steps ------------------------------------------------------------------

    def _mode_changed(self) -> None:
        side = self.side_button.isChecked()
        self.old_canvas.setVisible(side)
        self.split.setSizes([1, 1])
        self.refresh(recompare=False)
        if side:
            self.old_canvas.set_view_state(*self.canvas.view_state())

    def show_old(self) -> None:
        """Side by side, at the shape of the card shown."""
        mark = self._card_mark
        self.side_button.setChecked(True)
        if mark is not None:
            self._zoom_to(mark)

    def step(self, step: int) -> None:
        """Go to the next (1) or previous (-1) mark, round."""
        if self.marks:
            self.go_to((self.current + step) % len(self.marks) if self.current >= 0 else 0)

    def go_to(self, index: int) -> None:
        self.current = index
        mark = self.marks[index]
        self._update_legend()
        self._draw_marks()
        self._zoom_to(mark)
        self._show_card(mark)

    def _zoom_to(self, mark: Mark) -> None:
        x0, y0, x1, y1 = mark.box
        canvas = (
            self.old_canvas
            if mark.status == REMOVED and self.side_button.isChecked()
            else self.canvas
        )
        canvas.zoom_to(QRectF(x0, y0, max(x1 - x0, 1e-3), max(y1 - y0, 1e-3)))
        self._sync(canvas)

    def _sync(self, source: LayoutCanvas) -> None:
        """Side by side, the two canvases zoom and pan together."""
        if self._syncing or not self.old_canvas.isVisible():
            return
        self._syncing = True
        other = self.canvas if source is self.old_canvas else self.old_canvas
        other.set_view_state(*source.view_state())
        self._syncing = False
        if self._card_mark is not None:
            self._place_card(self._card_mark)

    # -- the card ---------------------------------------------------------------------------

    def _hovered(self, canvas: LayoutCanvas) -> None:
        pos = canvas.viewport().mapFromGlobal(QCursor.pos())
        found = canvas.mark_at(pos)
        if found is not None:
            index = next(
                i for i, m in enumerate(canvas.marks) if m is found
            )  # the canvas holds the drawn marks in order
            mark = self._mark_of(canvas, index)
            if mark is not None and mark is not self._card_mark:
                self._show_card(mark)
            return
        if self._card_mark is not None and not self._near_card(canvas, pos):
            self.card.hide()
            self._card_mark = None

    def _mark_of(self, canvas: LayoutCanvas, index: int) -> Mark | None:
        side = self.side_button.isChecked()
        if canvas is self.old_canvas:
            shown = [m for m in self.marks if m.status == REMOVED]
        else:
            shown = [m for m in self.marks if m.status != REMOVED or not side]
        return shown[index] if 0 <= index < len(shown) else None

    def _near_card(self, canvas: LayoutCanvas, pos: QPoint) -> bool:
        mark = self._card_mark
        at = canvas.mapFromScene(mark.box[2], mark.box[3])
        near = (at.x() - pos.x()) ** 2 + (at.y() - pos.y()) ** 2 <= CARD_REACH_PX**2
        inside = self.card.geometry().contains(canvas.viewport().mapTo(self, pos))
        return near or inside

    def _show_card(self, mark: Mark) -> None:
        self._card_mark = mark
        can_roll_back = (
            self.key == UNCOMMITTED and mark.change is not None and mark.status != REMOVED
        )
        self.card.fill(mark, can_roll_back, self.component)
        self._place_card(mark)
        self.card.show()
        self.card.raise_()

    def _place_card(self, mark: Mark) -> None:
        canvas = (
            self.old_canvas
            if mark.status == REMOVED and self.side_button.isChecked()
            else self.canvas
        )
        at = canvas.viewport().mapTo(self, canvas.mapFromScene(mark.box[2], mark.box[3]))
        x = at.x() + MARK_RADIUS_PX + 2
        y = at.y() - self.card.height() // 2
        if x + self.card.width() > self.width() - 4:  # no room on the right: on the left
            x = at.x() - MARK_RADIUS_PX - 2 - self.card.width()
        y = max(4, min(y, self.height() - self.card.height() - 4))
        self.card.move(x, y)

    def roll_back(self) -> None:
        mark = self._card_mark
        if mark is None or mark.change is None or self.comparison is None:
            return
        try:
            self.document.history.rollback_shape(self.comparison, mark.change)
        except ValueError as exc:
            self.error.emit(str(exc))
            return
        self.card.hide()
        self._card_mark = None

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.card.hide()
            self._card_mark = None
        return False

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._card_mark is not None:
            self._place_card(self._card_mark)


class _Card(QFrame):
    """What changed in one shape, over the canvas next to its mark."""

    show_old = Signal()
    roll_back = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("change-card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.lines = QLabel()
        self.lines.setTextFormat(Qt.TextFormat.RichText)
        self.links = QLabel()
        self.links.setTextFormat(Qt.TextFormat.RichText)
        self.links.linkActivated.connect(self._link)
        layout = QVBoxLayout(self)
        space = theme.metric("space_m")
        layout.setContentsMargins(space, space, space, space)
        layout.setSpacing(theme.metric("space_xs"))
        layout.addWidget(self.title)
        layout.addWidget(self.lines)
        layout.addWidget(self.links)

    def fill(self, mark: Mark, can_roll_back: bool, component: str) -> None:
        muted = theme.color("muted").name()
        status = {ADDED: "added", REMOVED: "removed", MOVED: "not edited"}.get(
            mark.status, "edited"
        )
        self.title.setText(
            f"<b>{_escape(mark.name)}</b> <span style='color:{muted}'>{status}</span>"
        )
        self.lines.setText("<br>".join(_escape(line) for line in mark.lines))
        accent = theme.color("accent").name()
        links = [] if mark.status == ADDED else ["<a href='old'>Show old</a>"]
        if can_roll_back:
            links.append("<a href='back'>Roll back this shape</a>")
        style = f"<style>a {{ color: {accent}; text-decoration: none; }}</style>"
        self.links.setText(style + "&nbsp;&nbsp;&nbsp;".join(links))
        self.links.setVisible(bool(links))
        self.adjustSize()

    def _link(self, which: str) -> None:
        (self.show_old if which == "old" else self.roll_back).emit()


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
