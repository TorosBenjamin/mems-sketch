"""Themes, icons, settings, gizmos, hover and the IDE-like chrome."""

import shutil
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QGraphicsPolygonItem, QMessageBox, QToolButton

from mems_sketch.core.shapes import KINDS, BooleanShape, RectShape, RefShape, wrap_shapes
from mems_sketch.gui import icons, theme
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.panels import DETAIL_ROLE
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
    window.document.nodes.add(RectShape(name="plate", layer="device", x0=0, y0=0, x1=40, y1=20))
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


def test_the_shape_under_the_cursor_is_outlined(window, qtbot):
    one_rect(window)
    window.tree.select_paths([])
    mouse(window.canvas, QEvent.Type.MouseMove, 30, 5, Qt.MouseButton.NoButton)
    assert window._hovered == ((0, 0),) and window.canvas._hover_item is not None
    mouse(window.canvas, QEvent.Type.MouseMove, 80, 80, Qt.MouseButton.NoButton)
    # moves within one frame are handled at the next frame (canvas/max_fps)
    qtbot.waitUntil(lambda: window.canvas._hover_item is None, timeout=1000)
    window.settings.set("canvas/hover_highlight", False)
    mouse(window.canvas, QEvent.Type.MouseMove, 30, 5, Qt.MouseButton.NoButton)
    assert window.canvas._hover_item is None


def test_shapes_are_found_a_few_pixels_away(window):
    """Issue #2: over thin comb fingers the outline flickered between the comb and
    nothing; a shape within a few pixels of the cursor is found too."""
    one_rect(window)  # 0..40 x 0..20 µm at 4 px per µm
    assert window.hit(40.5, 10) == ((0, 0),)  # 2 px beside the edge
    assert window.hit(43, 10) is None  # 12 px away
    window.canvas.set_view_state(0.5, 20, 10)  # zoomed out: the same pixels reach further
    assert window.hit(46, 10) == ((0, 0),)


def test_the_outline_stays_on_between_a_combs_fingers(window):
    from mems_sketch import ArrayModifier

    fingers = RectShape(
        name="comb", layer="device", x0=0, y0=0, x1=2, y1=30,
        modifiers=[ArrayModifier(columns=6, dx=10)],
    )  # fmt: skip
    window.document.nodes.add(fingers)
    window.canvas.set_view_state(4, 25, 15)
    window.tree.select_paths([])
    window._hover(1, 15)  # on a finger
    assert window._hovered == ((0, 0),)
    window._hover(6, 15)  # in a gap, 16 px from both fingers
    assert window._hovered == ((0, 0),)
    assert window.hit(6, 15) == ((0, 0),)  # a click selects what is outlined
    window._hover(6, 40)  # outside the comb
    assert window._hovered is None and window.hit(6, 15) is None


def test_the_scale_bar_is_a_whole_number_of_grid_steps(window):
    """Issue #3: the scale bar can be read against the grid."""
    canvas = window.canvas
    for zoom in (0.37, 1, 2.9, 13, 150):
        canvas.set_view_state(zoom, 0, 0)
        cells = canvas.scale_bar_length() / canvas.grid_step()
        assert cells == pytest.approx(round(cells)) and round(cells) in (1, 2, 5, 10)
        assert canvas.scale_bar_length() * canvas.pixels_per_um() <= 125


def test_there_is_no_axis_indicator(window):
    """Issue #6: the view cannot flip, so an x/y indicator says nothing."""
    assert "canvas/show_axis_gizmo" not in {setting.key for setting in SETTINGS}
    assert "show_axis_gizmo" not in window.canvas.options


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
    window.document.nodes.add(RectShape(name="bad", layer="device", x0=50, y0=0, x1=51, y1=5))
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


def test_canvas_caption_shows_the_component_and_view_mode(window):
    assert window.canvas._caption == ("top", "top component")
    assert window.canvas.mode_button.text() == "Drawn ▾"
    window.open_component("comb_drive")
    assert "read-only" in window.canvas._caption[1]


def test_every_shape_kind_has_an_icon():
    rects = [RectShape(layer="device", x0=0, y0=0, x1=1, y1=1) for _ in range(2)]
    shapes = [kind.default("device") for kind in KINDS if kind.category == "primitive"]
    shapes += [wrap_shapes(op, "w", rects) for kind in KINDS for op in kind.wraps]
    shapes.append(RefShape(component="rectangle"))
    assert {s.icon_name() for s in shapes} <= set(icons.ICONS)


# -- components explorer and shape list --------------------------------------------

EXAMPLES = Path(__file__).parent.parent / "examples"


@pytest.fixture
def resonator(window, tmp_path):
    for name in ("resonator", "libraries"):
        shutil.copytree(
            EXAMPLES / name, tmp_path / name, ignore=shutil.ignore_patterns(".mems-sketch")
        )
    window.open_project(str(tmp_path / "resonator"))
    return window


def explorer_item(window, *names):
    """The explorer item reached by expanding ``names`` from the project group."""
    item = window.components.tree.topLevelItem(0)
    for name in names:
        item.setExpanded(True)
        item = next(
            item.child(i)
            for i in range(item.childCount())
            if item.child(i).data(0, window.components.NAME_ROLE) == name
        )
    return item


def children(item) -> list[str]:
    return [item.child(i).text(0) for i in range(item.childCount())]


def test_the_explorer_lists_each_component_once_with_its_private_ones(resonator):
    resonator.document.components.new("clamp", owner="suspension")
    project = resonator.components.tree.topLevelItem(0)
    assert children(project) == ["Process", "top", "suspension"]  # not nested by use
    suspension = explorer_item(resonator, "suspension")
    assert suspension.data(0, DETAIL_ROLE) is None  # definitions: no counts
    assert "placed in top" in suspension.toolTip(0)
    assert "places serpentine_spring, anchor" in suspension.toolTip(0)
    assert children(suspension) == ["clamp"]
    clamp = explorer_item(resonator, "suspension", "suspension/clamp")
    assert "private to suspension" in clamp.toolTip(0)
    assert suspension.isExpanded()  # clamp is being edited
    suspension.setExpanded(False)
    suspension.setExpanded(True)  # opened by hand: remembered
    assert "project/suspension" in resonator.components.expanded
    assert resonator.editor_state()["collapsed"]["explorer"]


def test_the_explorer_copies_library_components_and_adds_libraries(resonator, tmp_path):
    from PySide6.QtWidgets import QMenu

    menu = QMenu()
    resonator.components._component_actions(menu, "std.perforated_plate")
    copy = next(a for a in menu.actions() if a.text() == "Copy into the project")
    copy.trigger()
    assert "perforated_plate" in resonator.document.project.components
    assert resonator.area.current.component == "perforated_plate"  # opened to edit
    resonator.components.add_library(str(tmp_path / "libraries" / "mems_std"))
    groups = [
        resonator.components.tree.topLevelItem(i).text(0)
        for i in range(resonator.components.tree.topLevelItemCount())
    ]
    assert groups == ["resonator", "std", "mems_std", "Built-in"]


def test_a_component_dropped_on_the_canvas_is_placed_there(resonator):
    resonator.open_component("suspension")
    canvas = resonator.canvas
    canvas.component_dropped.emit("anchor", 120.0, -40.0)
    placed = resonator.document.shapes[-1]
    assert (placed.component, placed.x, placed.y) == ("anchor", 120, -40)
    assert resonator.selection == [((0, len(resonator.document.shapes) - 1),)]


def test_open_in_the_other_pane(resonator):
    resonator.open_aside("suspension")
    assert resonator.area.split
    assert [v.component for v in resonator.area.views()] == ["top", "suspension"]


def test_a_new_library_has_no_top_and_its_tab_shows_a_component(window):
    window.new_library()
    assert window.document.project.top is None
    assert [v.component for v in window.area.views()] == ["component1"]
    window._edit_top()  # no top component to open
    assert "library" in window.statusBar().currentMessage()


def test_the_shape_list_shows_details_and_alignment_icons(resonator):
    tree = resonator.tree
    mass, comb = tree.topLevelItem(0), tree.topLevelItem(1)
    assert mass.data(0, DETAIL_ROLE) == "std.perforated_plate"
    assert mass.icon(tree.STATUS).isNull() and not comb.icon(tree.STATUS).isNull()
    assert comb.toolTip(tree.STATUS) == "Aligned: moving at mass.top"
    resonator.document.nodes.add_primitive("rect")
    assert tree.topLevelItem(tree.topLevelItemCount() - 1).data(0, DETAIL_ROLE) == "device"


def test_placed_components_open_read_only_in_the_shape_list(resonator):
    from mems_sketch.gui.panels import INSIDE_ROLE, PATH_ROLE, PLACES_ROLE

    tree = resonator.tree
    left = next(
        tree.topLevelItem(i)
        for i in range(tree.topLevelItemCount())
        if tree.topLevelItem(i).text(0) == "suspension_left"
    )
    assert left.data(0, PLACES_ROLE) == "suspension" and not left.isExpanded()
    left.setExpanded(True)  # loads what is inside
    inside = [left.child(i) for i in range(left.childCount())]
    assert [i.text(0) for i in inside] == ["spring", "anchor"]
    assert inside[0].data(0, INSIDE_ROLE) == ("suspension", ((0, 0),))
    assert not inside[0].flags() & Qt.ItemFlag.ItemIsSelectable  # read-only
    assert tree.opened["top"] == {left.data(0, PATH_ROLE)}  # remembered per component
    comb = tree.topLevelItem(1)
    assert comb.childCount() == 0  # a built-in has nothing inside to show
    resonator._tree_double_clicked(inside[1], 0)  # edit the anchor where it lives
    assert resonator.document.active == "suspension"
    assert resonator.selection == [((0, 1),)]


def menu_texts(menu) -> dict:
    found = {}
    for action in menu.actions():
        found[action.text()] = action
        if action.menu() is not None:
            found |= {f"{action.text()}/{k}": v for k, v in menu_texts(action.menu()).items()}
    return found


def test_private_components_from_the_explorer_menu(resonator, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    doc = resonator.document
    panel = resonator.components
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("clamp", True))
    menu_texts(panel.menu_for(explorer_item(resonator, "suspension")))[
        "New private component…"
    ].trigger()
    assert doc.active == "suspension/clamp"
    resonator.open_component("top")
    clamp = explorer_item(resonator, "suspension", "suspension/clamp")
    actions = menu_texts(panel.menu_for(clamp))
    assert not actions["Place in top"].isEnabled()  # private to suspension
    assert not clamp.flags() & Qt.ItemFlag.ItemIsDragEnabled
    assert "clamp" not in doc.component_names()
    actions["Make shared"].trigger()
    assert "clamp" in doc.project.components and "clamp" in doc.component_names()
    menu_texts(panel.menu_for(explorer_item(resonator, "clamp")))[
        "Make private to/suspension"
    ].trigger()
    assert "suspension/clamp" in doc.project.components
    assert resonator.area.current.component == "top"


def test_a_read_only_component_shows_its_interface(resonator):
    from PySide6.QtWidgets import QLabel, QLineEdit

    resonator.open_component("std.perforated_plate")
    tree = resonator.tree
    assert tree.topLevelItemCount() == 1
    assert tree.topLevelItem(0).data(0, DETAIL_ROLE) == "interface only"
    labels = [lab.text() for lab in resonator.properties.findChildren(QLabel)]
    assert "size" in labels and "pitch" in labels and "hole_r" not in labels  # internal
    assert not resonator.properties.findChildren(QLineEdit)  # text, not inputs
    assert resonator.parameters._names == ["size", "pitch"]
    assert resonator.hit(0, 0) is None
    resonator.settings.set("editor/show_implementation", True)
    assert tree.topLevelItem(0).data(0, DETAIL_ROLE) != "interface only"  # its shapes
    assert "hole_r" in resonator.parameters._names


def test_placed_library_components_do_not_open_up_in_the_shape_list(resonator):
    resonator.open_component("top")
    mass = next(
        resonator.tree.topLevelItem(i)
        for i in range(resonator.tree.topLevelItemCount())
        if resonator.tree.topLevelItem(i).text(0) == "mass"  # std.perforated_plate
    )
    assert mass.childCount() == 0
    resonator.settings.set("editor/show_implementation", True)
    mass = next(
        resonator.tree.topLevelItem(i)
        for i in range(resonator.tree.topLevelItemCount())
        if resonator.tree.topLevelItem(i).text(0) == "mass"
    )
    assert mass.childCount() == 1  # expands into its shapes when opened


def test_a_shape_cut_in_two_says_so_and_shows_its_box(window):
    # A slot right through a bar: one shape, two pieces. The list says so, and the
    # dashed box shows where its points (the centre in the gap) come from.
    bar = RectShape(layer="device", x0=0, y0=0, x1=100, y1=20)
    slot = RectShape(layer="device", x0=45, y0=-5, x1=55, y1=25)
    window.document.nodes.add(BooleanShape(name="cut", op="subtract", a=[bar], b=[slot]))
    item = window.tree.topLevelItem(0)
    assert item.data(0, DETAIL_ROLE) == "2 pieces · subtract"
    assert "2 separate pieces" in item.toolTip(0)

    window.tree.select_paths([((0, 0),)])
    boxes = [i for i in window.canvas._overlay if isinstance(i, QGraphicsPolygonItem)]
    assert boxes == []  # only while working with points
    window.tool_windows.open("points")
    boxes = [i for i in window.canvas._overlay if isinstance(i, QGraphicsPolygonItem)]
    assert len(boxes) == 1
    corners = boxes[0].polygon()
    assert (corners.boundingRect().left(), corners.boundingRect().right()) == (0, 100)

    notch = slot.model_copy(update={"y1": 10})
    window.document.nodes.replace(
        ((0, 0),), BooleanShape(name="cut", op="subtract", a=[bar], b=[notch])
    )
    assert "pieces" not in window.tree.topLevelItem(0).data(0, DETAIL_ROLE)  # a notch: one piece
