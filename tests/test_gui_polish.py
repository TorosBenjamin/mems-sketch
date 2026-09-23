"""Themes, icons, settings, gizmos, hover and the IDE-like chrome."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QToolButton

from mems_sketch.core.shapes import KINDS, RectShape, RefShape, wrap_shapes
from mems_sketch.gui import icons, theme
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.settings import SETTINGS, PreferencesDialog, Settings

NONE = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    return w


def mouse(canvas, kind, x, y, buttons=LEFT):
    pos = QPointF(canvas.mapFromScene(QPointF(x, y)))
    button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else LEFT
    event = QMouseEvent(kind, pos, canvas.viewport().mapToGlobal(pos), button, buttons, NONE)
    QApplication.sendEvent(canvas.viewport(), event)


def drag(canvas, start, end):
    mouse(canvas, QEvent.Type.MouseButtonPress, *start)
    for k in range(1, 6):
        t = k / 5
        mouse(
            canvas,
            QEvent.Type.MouseMove,
            start[0] + (end[0] - start[0]) * t,
            start[1] + (end[1] - start[1]) * t,
        )
    mouse(canvas, QEvent.Type.MouseButtonRelease, *end, Qt.MouseButton.NoButton)


def one_rect(window):
    window.document.add_shape(RectShape(name="plate", layer="device", x0=0, y0=0, x1=40, y1=20))
    window.canvas.set_view_state(4, 20, 10)  # 4 px per µm around the plate
    window.tree.select_paths([((0, 0),)])


# -- icons and themes ----------------------------------------------------------


@pytest.mark.parametrize("name", ["light", "dark"])
def test_every_icon_draws_something_in_both_themes(qapp, name):
    icons.set_theme(name)
    try:
        for icon_name in icons.ICONS:
            image = icons.pixmap(icon_name, 16).toImage()
            painted = any(
                image.pixelColor(x, y).alpha() > 0
                for x in range(image.width())
                for y in range(image.height())
            )
            assert painted, icon_name
    finally:
        icons.set_theme("light")


def test_the_interface_theme_is_a_setting_applied_at_once(window):
    window.settings.set("appearance/ui_theme", "dark")
    assert (
        QApplication.instance().palette().window().color().name() == theme.TOKENS["dark"]["window"]
    )
    assert window.canvas.backgroundBrush().color().name() == "#1e1f22"  # the canvas follows
    window.settings.set("appearance/canvas_theme", "light")  # ... unless chosen separately
    assert window.canvas.backgroundBrush().color().name() == "#ffffff"
    window.settings.set("appearance/ui_theme", "light")
    assert QApplication.instance().palette().window().color().name() == "#f7f8fa"


# -- settings --------------------------------------------------------------------


def test_settings_have_defaults_types_and_limits(qapp):
    settings = Settings()
    assert settings.get("snapping/distance_px") == 10
    settings.set("snapping/distance_px", "25")
    assert settings.get("snapping/distance_px") == 25
    settings.set("snapping/distance_px", 1000)  # clamped to the maximum
    assert settings.get("snapping/distance_px") == 40
    settings.set("appearance/ui_theme", "purple")  # not a choice: the default
    assert settings.get("appearance/ui_theme") == "system"
    changed = []
    settings.changed.connect(changed.append)
    settings.reset()
    assert settings.get("snapping/distance_px") == 10 and "snapping/distance_px" in changed
    assert Settings().get("snapping/distance_px") == 10


def test_the_old_canvas_theme_setting_is_carried_over(qapp):
    QSettings("mems-sketch", "mems-sketch").setValue("canvas/theme", "dark")
    assert Settings().get("appearance/canvas_theme") == "dark"


def test_the_settings_dialog_edits_and_searches(window):
    dialog = PreferencesDialog(window.settings, [("Duplicate", "Ctrl+D")])
    dialog._editors["canvas/show_grid"].setChecked(False)
    assert window.canvas.options["show_grid"] is False  # applied at once
    dialog.search.setText("snap distance")
    assert dialog.pages.currentItem().text() == "Snapping"
    visible = [k for k, row in dialog._rows.items() if not row[-1].isHidden()]
    assert visible == ["snapping/distance_px"]
    dialog.search.setText("")
    dialog.pages.setCurrentRow(1)  # Canvas
    dialog._reset_page()
    assert window.canvas.options["show_grid"] is True
    assert len(dialog._rows) == len(SETTINGS)


def test_snapping_can_be_switched_off(window):
    one_rect(window)
    tool = window.tools["measure"]
    near = tool.snap(40.3, 19.8, [("corner", 40, 20)], NONE)
    assert near[:2] == (40, 20)
    window.settings.set("snapping/points", False)
    window.settings.set("snapping/grid", False)
    assert tool.snap(40.3, 19.8, [("corner", 40, 20)], NONE)[:2] == (40.3, 19.8)


# -- gizmos and hover --------------------------------------------------------------


def test_move_gizmo_arrow_moves_along_one_axis_only(window):
    one_rect(window)
    window.set_tool("move")
    kind, cx, cy = window.canvas.gizmo
    assert (kind, cx, cy) == ("move", 20, 10)
    grab = cx + 40 / window.canvas.pixels_per_um()  # on the x arrow
    assert window.canvas.gizmo_hit(grab, cy) == "x"
    drag(window.canvas, (grab, cy), (grab + 10.3, cy + 7))
    plate = window.document.node(((0, 0),))
    assert (plate.x0, plate.y0) == (10, 0)  # moved 10 in x (grid), nothing in y


def test_move_gizmo_centre_moves_freely(window):
    one_rect(window)
    window.set_tool("move")
    assert window.canvas.gizmo_hit(20, 10) == "free"
    drag(window.canvas, (20, 10), (25, 30))
    plate = window.document.node(((0, 0),))
    assert (plate.x0, plate.y0) == (5, 20)


def test_rotate_ring_rotates_about_the_centre_in_steps(window):
    one_rect(window)
    window.set_tool("rotate")
    _, cx, cy = window.canvas.gizmo
    radius = 0.8 * window.canvas.options["gizmo_size_px"] / window.canvas.pixels_per_um()
    assert window.canvas.gizmo_hit(cx + radius, cy) == "ring"
    mouse(window.canvas, QEvent.Type.MouseButtonPress, cx + radius, cy)
    mouse(window.canvas, QEvent.Type.MouseMove, cx, cy + radius)  # a quarter turn
    assert window.canvas._gizmo_sweep == (0.0, 90.0)
    mouse(window.canvas, QEvent.Type.MouseButtonRelease, cx, cy + radius, Qt.MouseButton.NoButton)
    node = window.document.node(((0, 0),))
    assert node.kind == "transform" and node.rotation == 90  # a primitive gets wrapped


def test_gizmos_can_be_hidden_and_are_not_shown_on_read_only_tabs(window):
    one_rect(window)
    window.set_tool("move")
    assert window.canvas.gizmo is not None
    window.setting_actions["canvas/show_gizmos"].trigger()
    assert window.canvas.gizmo is None
    window.settings.set("canvas/show_gizmos", True)
    window.set_tool("select")
    assert window.canvas.gizmo is None  # Select has no gizmo
    window.open_component("comb_drive")
    window.set_tool("move")
    assert window.canvas.gizmo is None


def test_the_shape_under_the_cursor_is_outlined(window):
    one_rect(window)
    window.tree.select_paths([])
    mouse(window.canvas, QEvent.Type.MouseMove, 30, 5, Qt.MouseButton.NoButton)
    assert window._hovered == ((0, 0),) and window.canvas._hover_item is not None
    mouse(window.canvas, QEvent.Type.MouseMove, 80, 80, Qt.MouseButton.NoButton)
    assert window.canvas._hover_item is None
    window.settings.set("canvas/hover_highlight", False)
    mouse(window.canvas, QEvent.Type.MouseMove, 30, 5, Qt.MouseButton.NoButton)
    assert window.canvas._hover_item is None


# -- chrome --------------------------------------------------------------------


def test_find_action_filters_and_runs(window):
    one_rect(window)
    window.find_action()
    dialog = window._find_dialog
    dialog.search.setText("dupl")
    shown = [item.text(0) for item in dialog._visible()]
    assert shown == ["Duplicate"]
    dialog._run(dialog.list.currentItem())
    assert [s.name for s in window.document.shapes] == ["plate", "plate1"]


def test_tool_options_follow_the_tool(window):
    window.set_tool("path")
    assert window.tool_name.text().strip() == "Path"
    assert all(a.isVisible() for a in window.tool_widgets["width"])
    window.set_tool("rotate")
    assert not any(a.isVisible() for a in window.tool_widgets["layer"])
    window.angle_box.setValue(30)
    assert window.settings.get("snapping/angle_step") == 30
    assert window.tools["rotate"].step == 30


def test_status_bar_shows_problems_zoom_and_grid(window):
    one_rect(window)
    assert window.problems_button.text() == "No problems"
    window.document.add_shape(RectShape(name="bad", layer="device", x0=50, y0=0, x1=51, y1=5))
    assert "violation" in window.problems_button.text()
    assert window.grid_label.text() == f"grid {window.canvas.grid_step():g} µm"
    assert "px/µm" in window.zoom_label.text()


def test_tabs_have_icons_a_close_button_and_a_menu(window):
    window.open_component("comb_drive")
    pane = window.area.panes[0]
    assert pane.count() == 2 and not pane.tabIcon(1).isNull()
    assert "read-only" in pane.tabToolTip(1)
    close = pane.tabBar().tabButton(1, pane.tabBar().ButtonPosition.RightSide)
    assert isinstance(close, QToolButton)
    window.area.close_others(window.area.views()[0])
    assert [v.component for v in window.area.views()] == ["top"]
    close = pane.tabBar().tabButton(0, pane.tabBar().ButtonPosition.RightSide)
    close.click()  # closing the last tab opens the top component again
    assert [v.component for v in window.area.views()] == ["top"]


def test_canvas_caption_and_palette_labels(window):
    assert window.canvas._caption == ("top", "Drawn · top component")
    window.open_component("comb_drive")
    assert "read-only" in window.canvas._caption[1]
    window.settings.set("appearance/palette_labels", True)
    assert window.palette.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextUnderIcon


def test_every_shape_kind_has_an_icon():
    rects = [RectShape(layer="device", x0=0, y0=0, x1=1, y1=1) for _ in range(2)]
    shapes = [kind.default("device") for kind in KINDS if kind.category == "primitive"]
    shapes += [wrap_shapes(op, "w", rects) for kind in KINDS for op in kind.wraps]
    shapes.append(RefShape(component="rectangle"))
    assert {s.icon_name() for s in shapes} <= set(icons.ICONS)
