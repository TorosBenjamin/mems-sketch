# Editor Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The main window of the spec `docs/superpowers/specs/2026-09-23-editor-layout-design.md`: one toolbar row, IntelliJ-style tool windows, canvas modes and a right-click menu on the canvas, a Process tab, built as separate modules.

**Architecture:** `MainWindow` (`gui/app.py`) is split. Commands and menus go to `gui/actions.py`, tool windows to `gui/toolwindows.py`, the toolbar row to `gui/toolbar.py`, the status bar to `gui/statusbar.py`, and the Process tab to `gui/process_view.py`. `gui/canvas.py` gains its overlays and right-click handling. `MainWindow` composes them and keeps selection, hit-testing and running commands.

**Tech Stack:** PySide6, pytest-qt (offscreen), ruff.

**Commands** (repo root): tests `uv run --extra dev python -m pytest -q -p no:cacheprovider`; lint `uv run --extra dev ruff check . && uv run --extra dev ruff format --check .`; screenshot `uv run --extra dev python $SCRATCH/shot.py <out.png>` (Task 0).

Work in a worktree on `feature/editor-layout` from `development`. Baseline: 279 passed.

Because this is a large GUI change, steps name the exact code to move and the exact APIs to add. New behaviour comes with its test code; moved code is moved, not rewritten.

---

### Task 0: Screenshot helper (scratch, not committed)

`$SCRATCH/shot.py` opens the resonator example in a `MainWindow` offscreen at 1500×950, processes events, and saves `window.grab()` to the given path. It is used to look at the result of each task once, and in Task 6 for `docs/screenshot.png`.

```python
import shutil, sys, tempfile
from pathlib import Path
import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
from mems_sketch.gui.app import MainWindow

repo = Path(__file__).resolve().parents[0]
app = QApplication([])
tmp = Path(tempfile.mkdtemp())
for name in ("resonator", "libraries"):
    shutil.copytree(
        Path(sys.argv[2] if len(sys.argv) > 2 else ".") / "examples" / name,
        tmp / name,
        ignore=shutil.ignore_patterns(".mems-sketch"),
    )
w = MainWindow()
w.resize(1500, 950)
w.show()
w.open_project(str(tmp / "resonator"))
w.open_component("top")
for _ in range(20):
    app.processEvents()
w.grab().save(sys.argv[1])
```

---

### Task 1: Commands and menus in `gui/actions.py`

Move `_action`, `_build_actions` (menus, tool actions, overlay/setting actions, dark canvas), `_fill_component_menu`, `_tab_menu` and the constants `PRIMITIVES`, `OPERATIONS`, `OVERLAYS` from `app.py` into `gui/actions.py`:

- `make_action(parent, text, slot, shortcut=None, menu=None, icon=None) -> QAction` (the former `_action`).
- `class Actions` built by `Actions(window)`: holds every action the window used as an attribute (`save`, `undo`, `redo`, `make`, `unpack`, `rotate_left`, `rotate_right`, `mirror_h`, `mirror_v`, `find`, `settings`, `split`, `dark`, `tools: dict[str, QAction]`, `operations: dict[str, QAction]`, `settings_toggles: dict[str, QAction]`) and the menus: `file`, `edit`, `tools_menu`, `insert`, `add` (primitives), `place` (components, filled on `aboutToShow`), `operations_menu`, `view`, `help`, and `root` (a `QMenu` holding them in that order).
- `all_actions() -> list[QAction]`: every leaf action, for registering on the window.

`MainWindow` keeps its attribute names as properties or plain aliases (`self.undo_action = self.actions.undo`, …), so the rest of `app.py`, the tools and the tests keep working. In this task the menu bar is still built: `for menu in actions.root_menus: self.menuBar().addMenu(menu)`.

`find_action.menu_actions` accepts a `QMenu` as well as a `QMenuBar` (both have `actions()`); callers pass `self.actions.root`.

Verify: the full suite passes unchanged; ruff clean. Commit: "Move the window's commands and menus into gui/actions.py".

---

### Task 2: Tool windows in `gui/toolwindows.py`

**API:**

```python
ANCHORS = ("left-top", "left-bottom", "bottom", "right")


class ToolWindows(QWidget):
    changed = Signal()  # a window opened or closed, or a panel was resized

    def __init__(self, editor: QWidget, parent: QWidget | None = None): ...
    def add(self, name: str, title: str, icon: str, widget: QWidget, anchor: str) -> None: ...
    def open(self, name: str) -> None: ...  # shows it; closes the other window of its anchor
    def close(self, name: str) -> None: ...
    def toggle(self, name: str) -> None: ...
    def is_open(self, name: str) -> bool: ...
    def button(self, name: str) -> QToolButton: ...
    def state(
        self,
    ) -> dict: ...  # {"open": [...], "sizes": {"main": [...], "left": [...], "center": [...]}}
    def restore(self, state: dict) -> None: ...  # ignores unknown names and bad sizes
```

**Structure:**
- left stripe | `QSplitter` (horizontal: left side = vertical splitter [host left-top, host left-bottom]; centre = vertical splitter [editor, host bottom]; host right) | right stripe.
- A host is a frame with a header (title label, object name `tool-window-title`, plus a hide button) and a `QStackedWidget` of its windows.
- Stripes: the left stripe has groups left-top, a 14 px gap, left-bottom, a stretch, then bottom. The right stripe has group right. The buttons are checkable, auto-raise `QToolButton`s with the window's icon and its title as tooltip.
- A host is visible when it has an open window, and the left side when either of its hosts is.

`app.py`: `_build_docks` goes. The body is `self.tool_windows = ToolWindows(self.area)`, set as the central widget.
- Add Components (`left-top`), Shapes and Layers (`left-bottom`), Messages (`bottom`), Properties, Parameters and Points (`right`).
- Constants stay a right window until Task 5.
- First start opens components, shapes, properties and messages.
- State is saved as JSON under the app setting `layout/tool_windows` on `changed` and on close, and restored at start.
- `_show_messages_panel` becomes `self.tool_windows.open("messages")`.
- View → Panels lists a checkable action per window that calls `toggle`.

Add the missing icons to `gui/icons.py` `ICONS`, in its SVG style: `shapes`, `parameters`, `messages`, `properties` (the drawings from the mockup).

**Tests** (`tests/test_gui_layout.py`, with the `window` fixture used by `tests/test_gui_polish.py`):

```python
def test_stripe_buttons_open_and_close_tool_windows(window):
    tw = window.tool_windows
    assert tw.is_open("components") and tw.is_open("shapes") and tw.is_open("properties")
    tw.button("parameters").click()
    assert tw.is_open("parameters") and not tw.is_open("properties")  # one per anchor
    tw.button("parameters").click()
    assert not tw.is_open("parameters")


def test_the_left_side_splits_between_its_two_anchors(window):
    tw = window.tool_windows
    tw.open("layers")
    assert tw.is_open("components") and tw.is_open("layers") and not tw.is_open("shapes")
    tw.close("components")
    tw.close("layers")
    assert not tw.left_side.isVisible()


def test_open_tool_windows_are_remembered(window, qtbot):
    window.tool_windows.open("points")
    window.close()
    again = MainWindow()
    qtbot.addWidget(again)
    assert again.tool_windows.is_open("points")


def test_the_problem_count_opens_messages(window):
    window.tool_windows.close("messages")
    window.problems_button.click()
    assert window.tool_windows.is_open("messages")
```

Update `tests/test_gui_window.py` lines 114–121 (docks and View menu) to use `window.tool_windows`. Verify, look at a screenshot once, commit: "Tool windows opened from stripes replace the docks".

---

### Task 3: One toolbar row and the status bar

`gui/toolbar.py`: `build_toolbar(window) -> QToolBar` (not movable, 18 px icons). It holds:
- a ☰ `QToolButton` with `actions.root` as its instant-popup menu;
- a project `QLabel` (object name `heading`), updated by `_update_title`;
- undo, redo;
- menu buttons with text beside icon: *Add* (`actions.add`), *Place* (`actions.place`), *Operations* (`actions.operations_menu`);
- a stretch, then split, find, settings.

The menu bar is no longer created. Every action from `actions.all_actions()` is added to the window with `self.addActions(...)`, so shortcuts work. `show_settings` and `find_action` read `actions.root`.

`actions.add` holds Rectangle, Circle, Polygon and Path, which start the drawing tools (`window.set_tool(kind)`), and Arc (`window.add_primitive("arc")`). ☰ → Insert shows the same `add` and `place` menus. `actions.operations_menu` has a *Combine* submenu (union, subtract, intersect, xor), then offset, fillet, transform, layer map, a separator, then make component and unpack.

`gui/statusbar.py`: `ToolStatus(QWidget)` holds the tool options, moved from `_build_tool_options`: `layer_box`, `width_box`, `angle_box` with their captions, the snap toggles and the gizmo toggle. `show_for(tool)` shows what the tool uses. `_build_status_bar` adds it as the first permanent widget, before the problems button, grid, zoom and coordinates. The tool-options toolbar and `addToolBarBreak` go. `MainWindow` keeps `self.layer_box`, `self.width_box` and `self.angle_box` as references to the status-bar widgets, and `tool_name` as a label in `ToolStatus` (tests use it).

**Tests** (append to `tests/test_gui_layout.py`):

```python
def test_one_toolbar_row_and_no_menu_bar(window):
    bars = window.findChildren(QToolBar)
    assert len([b for b in bars if b.isVisible()]) == 1
    assert window.menuWidget() is None or not window.menuBar().actions()


def test_every_menu_shortcut_still_works(window):
    from mems_sketch.gui.find_action import menu_actions

    registered = set(window.actions())
    with_keys = [a for _, a in menu_actions(window.actions_.root) if not a.shortcut().isEmpty()]
    assert with_keys and all(a in registered for a in with_keys)


def test_add_starts_the_drawing_tool(window):
    next(a for a in window.actions_.add.actions() if a.text() == "Rectangle").trigger()
    assert window.tool.name == "rect"
```

(`window.actions_` is the `Actions` object; `actions` is taken by `QWidget.actions()`.)

Verify, screenshot, commit: "One toolbar row with a main menu; tool options in the status bar".

---

### Task 4: The canvas: modes, zoom, caption, right-click

`gui/canvas.py`:
- **Mode palette:** `set_mode_actions(actions: list[QAction])` builds a vertical overlay in the top-right corner, one `QToolButton` per action (`setDefaultAction`, so the checked state follows). `MainWindow._render` passes the six mode actions (`select`, `hand`, `move`, `rotate`, `align`, `measure`).
- **Zoom:** the zoom box moves to the bottom-right corner (`resizeEvent`).
- **Caption:** a small overlay widget replaces the painted caption. It holds the component name (bold), a view-mode `QToolButton` with a menu, and the muted details. `set_caption(title, subtitle)` keeps its signature. `set_view_modes(modes: dict[str, str], current: str)` fills the button. New signal `mode_chosen = Signal(str)`.
- **Right button:** the press is remembered and panning starts only after 4 px of movement. A release without panning emits `context_requested = Signal(float, float, QPoint)` (µm position, global position). The middle button and Space+drag pan at once, as before.

`app.py`:
- `set_view_mode(mode)` replaces `_mode_changed`: it sets the current view's mode, re-renders it and updates the caption.
- `_context_menu(view, x, y, global_pos)` selects the shape under the cursor if it isn't already selected. It then shows `actions.context_menu(selection_nonempty)`: *Add ▸* (drawing tools start with their first point at `(x, y)`), *Place component ▸*, operations, *Rotate & mirror ▸*, duplicate, delete. Entries that don't apply are disabled.
- The palette toolbar (`_build_palette`, `_palette_style`) and the `appearance/palette_labels` setting go.

Drawing from the right-click menu: `DrawTool.start_at(x, y)` (new in `gui/tools.py`) places the first point as a click at `(x, y)` would.

**Tests** (append):

```python
def right_click(
    canvas, pos, drag=QPoint(0, 0)
): ...  # press RightButton at pos, move to pos + drag, release (QMouseEvent on the viewport)


def test_right_click_opens_the_menu_and_right_drag_pans(window, monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMenu, "exec", lambda self, *a: shown.append([x.text() for x in self.actions()])
    )
    canvas = window.canvas
    centre = canvas.viewport().rect().center()
    right_click(canvas, centre)
    assert shown and "Add" in shown[0]
    before = canvas.mapToScene(centre)
    right_click(canvas, centre, QPoint(40, 0))
    assert len(shown) == 1 and canvas.mapToScene(centre) != before


def test_right_click_add_rectangle_starts_at_the_click(
    window, monkeypatch
): ...  # trigger the context menu's Add ▸ Rectangle at (x, y); the rect tool's start is (x, y)


def test_mode_palette_switches_tools(window):
    button = next(
        b
        for b in window.canvas.mode_palette.findChildren(QToolButton)
        if b.defaultAction().text() == "Measure"
    )
    button.click()
    assert window.tool.name == "measure"


def test_the_caption_changes_the_view_mode(window):
    window.canvas.mode_chosen.emit("etched")
    assert window.view.view_mode == "etched"
```

Update the tests that used `mode_box` (`test_gui_tools.py:209`, `test_gui_window.py:109`) to call `set_view_mode`, and `test_canvas_caption_and_palette_labels` to drop the palette part. Verify, screenshot, commit: "Canvas modes, zoom and view mode on the canvas; right-click menu".

---

### Task 5: The Process tab

- `gui/process_view.py`: `ProcessView(QWidget)` holds the constants table (`ConstantsPanel`, moved in) and the layer definitions (name, GDS layer/datatype, undercut, from `LayersPanel`).
- `LayersPanel` keeps visibility and colour (and choosing the draw layer).
- `EditorArea`:
  - `open_process()` opens or raises the one Process tab in the current pane;
  - `views()` returns component views only;
  - `layout_state()` lists the Process tab as `{"process": true}` in its pane.
- `MainWindow`:
  - restores it in `_apply_state`;
  - the Components panel gets a *Process* item that opens it;
  - ☰ → View → Process opens it;
  - the Constants tool window goes.

**Tests** (append):

```python
def test_process_tab_opens_edits_with_undo_and_is_remembered(window, qtbot):
    ...  # open the example; window.area.open_process(); set a constant through the tab's table;
    # undo restores it; close and reopen the window: the Process tab is back


def test_layers_window_is_visibility_only(
    window,
): ...  # the Layers window has no GDS/undercut columns; the Process tab has them
```

Update any test that used the Constants panel or the layer definition columns. Verify, screenshot, commit: "Process constants and layer definitions in a Process tab".

---

### Task 6: Screenshot and README

- Render `docs/screenshot.png` with `shot.py` (the resonator example, top component, comb_top selected).
- The README's GUI section describes the layout: the toolbar row, the stripes, the canvas modes, right-click, the Process tab.

Commit: "New screenshot and README for the editor layout".
