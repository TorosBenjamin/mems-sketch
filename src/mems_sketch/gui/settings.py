"""Preferences: per-user settings with defaults, and the dialog that edits them.

Settings live in :class:`QSettings` (per user, not in the project). Each one is
declared once in :data:`SETTINGS` with its default, type and where it appears
in the dialog, so reading a setting never needs to know its default:

    settings = Settings()
    settings.get("snapping/distance_px")   # -> 10 unless changed

``Settings.changed`` is emitted with the key whenever a value changes, and the
window applies it at once (there is no restart and no "Apply" step).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QObject, QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.gui import icons, theme, widgets

ORGANIZATION = APPLICATION = "mems-sketch"


def canvas_themes() -> dict[str, str]:
    """appearance/canvas_theme: the interface's theme, or any theme's canvas colours."""
    return {"auto": "Same as the interface"} | {t.id: t.name for t in theme.themes().values()}


@dataclass(frozen=True)
class Setting:
    key: str
    default: Any
    label: str
    page: str
    group: str
    help: str = ""
    # value -> label, or a function giving them when they are found at run time (themes)
    choices: dict[str, str] | Callable[[], dict[str, str]] | None = None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    suffix: str = ""
    decimals: int = 0
    keywords: tuple[str, ...] = field(default_factory=tuple)
    special: str = ""  # what the minimum is shown as, e.g. "Follows the zoom" for 0

    def options(self) -> dict[str, str] | None:
        return self.choices() if callable(self.choices) else self.choices


SCROLLING = {  # canvas/scrolling: value -> label
    "auto": "Automatic: a mouse wheel zooms, a touchpad pans",
    "zoom": "Scrolling zooms",
    "pan": "Scrolling pans (Ctrl zooms)",
}

EMPTY_DRAG = {  # canvas/empty_drag: value -> label
    "box": "Draws a selection box",
    "pan": "Pans the view (Shift: a selection box)",
}

BOX_SELECTION = {  # editor/box_selection: value -> label
    "touching": "What the box touches",
    "inside": "What is entirely inside the box",
    "direction": "By direction: to the right inside, to the left touching",
}

FRAME_RATES = {  # canvas/max_fps: value -> label
    "display": "The display's refresh rate",
    "30": "30 fps",
    "60": "60 fps",
    "120": "120 fps",
    "144": "144 fps",
    "240": "240 fps",
    "0": "No limit",
}


SETTINGS: tuple[Setting, ...] = (
    # -- Appearance ---------------------------------------------------------
    Setting(
        "appearance/ui_theme",
        "system",
        "Interface theme",
        "Appearance",
        "Theme",
        "More themes come from packages and from your themes folder: JSON files that "
        "can change some colours of another theme. The user guide explains how.",
        choices=theme.choices,
        keywords=("dark", "light", "colour", "color"),
    ),
    Setting(
        "appearance/canvas_theme",
        "auto",
        "Canvas background",
        "Appearance",
        "Theme",
        "The layout canvas can stay light in a dark interface, or the other way round.",
        choices=canvas_themes,
        keywords=("dark", "white", "background"),
    ),
    Setting(
        "appearance/custom_title_bar",
        True,
        "Use the toolbar as the title bar",
        "Appearance",
        "Window",
        "The toolbar holds minimize, maximize and close instead of the system's title bar: "
        "drag an empty part of it to move the window, double-click it to maximize.",
        keywords=("title bar", "frame", "window", "close", "minimize", "maximize"),
    ),
    # -- Canvas -------------------------------------------------------------
    Setting(
        "appearance/reduce_motion",
        False,
        "Reduce motion",
        "Appearance",
        "Motion",
        "Jump instead of animating: the canvas moving to a place, notes fading in and "
        "out, a shape picked in a panel glowing.",
        keywords=("animation", "animate", "accessibility"),
    ),
    Setting(
        "canvas/fill_opacity",
        45,
        "Layer fill opacity",
        "Canvas",
        "Drawing",
        minimum=0,
        maximum=100,
        step=5,
        suffix=" %",
    ),
    Setting(
        "canvas/outline_width",
        1.0,
        "Layer outline width",
        "Canvas",
        "Drawing",
        minimum=0.5,
        maximum=4,
        step=0.5,
        suffix=" px",
        decimals=1,
    ),
    Setting(
        "canvas/hover_highlight",
        True,
        "Highlight the shape under the cursor",
        "Canvas",
        "Drawing",
        keywords=("hover", "pre-selection"),
    ),
    Setting("canvas/show_grid", True, "Show the grid", "Canvas", "Grid"),
    Setting(
        "canvas/grid_spacing_px",
        12,
        "Closest grid lines",
        "Canvas",
        "Grid",
        "The grid takes the smallest 1-2-5 step that keeps lines at least this far apart.",
        minimum=6,
        maximum=80,
        suffix=" px",
    ),
    Setting(
        "canvas/show_axes",
        True,
        "Colour the x and y axes",
        "Canvas",
        "Grid",
        "Red x and green y through the origin, as in Blender and Unity.",
    ),
    Setting(
        "canvas/show_scale_bar",
        True,
        "Show the scale bar",
        "Canvas",
        "Overlays",
    ),
    Setting(
        "canvas/always_show_points",
        False,
        "Always show points",
        "Canvas",
        "Overlays",
        "Otherwise points show while the Points panel is open or you are aligning.",
        keywords=("points", "markers", "labels"),
    ),
    Setting(
        "canvas/show_gizmos",
        True,
        "Show move and rotate handles on the selection",
        "Canvas",
        "Overlays",
        "Drag an arrow to move along one axis, the centre to move freely, the ring to rotate.",
        keywords=("gizmo", "handles", "transform"),
    ),
    Setting(
        "canvas/gizmo_size_px",
        70,
        "Handle size",
        "Canvas",
        "Overlays",
        minimum=40,
        maximum=160,
        step=5,
        suffix=" px",
        keywords=("gizmo",),
    ),
    Setting(
        "canvas/scrolling",
        "auto",
        "Scrolling",
        "Canvas",
        "Navigation",
        "What scrolling does on the canvas. A touchpad pans with two fingers and zooms "
        "with a pinch; a mouse wheel zooms. Choose one if your device is taken for the "
        "other.",
        choices=SCROLLING,
        keywords=("touchpad", "trackpad", "pinch", "pan", "wheel", "mouse"),
    ),
    Setting(
        "canvas/empty_drag",
        "box",
        "Dragging on empty canvas",
        "Canvas",
        "Navigation",
        "In the Select tool. Pan suits a touchpad's tap-and-drag, which reaches the app "
        "as a left-button drag; Shift+drag then draws the selection box. Panning also "
        "works with the middle button, Space+drag, the Hand tool and two fingers.",
        choices=EMPTY_DRAG,
        keywords=("touchpad", "trackpad", "tap", "drag", "pan", "box", "select"),
    ),
    Setting(
        "canvas/zoom_step",
        1.25,
        "Zoom per wheel step",
        "Canvas",
        "Navigation",
        minimum=1.05,
        maximum=2.0,
        step=0.05,
        suffix=" ×",
        decimals=2,
    ),
    Setting(
        "canvas/max_fps",
        "display",
        "Frame rate limit",
        "Canvas",
        "Performance",
        "How often the canvas follows the mouse. Lower saves work on slower computers; "
        "the display's rate is as smooth as the screen can show.",
        choices=FRAME_RATES,
        keywords=("fps", "refresh", "smooth", "lag", "speed"),
    ),
    Setting(
        "canvas/draft_quality",
        True,
        "Draft quality while zooming and resizing",
        "Canvas",
        "Performance",
        "On designs too big to redraw smoothly, draw without smoothing during a zoom "
        "or resize, then in full quality once it stops. Smaller designs always stay "
        "in full quality.",
        keywords=("antialiasing", "fps", "lag", "speed"),
    ),
    # -- Snapping -----------------------------------------------------------
    Setting(
        "snapping/points",
        True,
        "Snap to shape points",
        "Snapping",
        "Snapping",
        "Corners, centres, edge middles and declared points.",
        keywords=("magnet",),
    ),
    Setting("snapping/grid", True, "Snap to the grid", "Snapping", "Snapping"),
    Setting(
        "snapping/grid_step",
        0.0,
        "Grid step",
        "Snapping",
        "Snapping",
        "What drawing and moving snap to. Empty: the grid drawn, finer as you zoom in. "
        "This is the editor's only: the geometry keeps 1 nm, and an export rounds to "
        "its own grid (the process's).",
        minimum=0.0,
        maximum=1e4,
        step=0.5,
        suffix=" µm",
        decimals=3,
        keywords=("precision", "resolution", "fixed grid"),
        special="Follows the zoom",
    ),
    Setting(
        "snapping/distance_px",
        10,
        "Snap distance",
        "Snapping",
        "Snapping",
        minimum=2,
        maximum=40,
        suffix=" px",
    ),
    Setting(
        "snapping/angle_step",
        15.0,
        "Rotation steps",
        "Snapping",
        "Rotation",
        minimum=1,
        maximum=90,
        step=1,
        suffix=" °",
        decimals=1,
    ),
    # -- Editor -------------------------------------------------------------
    Setting(
        "editor/show_implementation",
        False,
        "Show how library components are built",
        "Editor",
        "Components",
        "A component you cannot edit shows its interface: public parameters, points and "
        "its geometry. On: also its shapes, guides, internal parameters and private "
        "components (read-only).",
        keywords=("read-only", "library", "internal", "private", "shapes"),
    ),
    Setting(
        "editor/restore_state",
        True,
        "Reopen tabs, views and rulers with the project",
        "Editor",
        "Projects",
        "Kept in .mems-sketch/state.json in the project folder (ignored by git).",
    ),
    Setting(
        "editor/box_selection",
        "touching",
        "Box selection selects",
        "Editor",
        "Selection",
        "Dragging a box over the canvas selects the shapes it touches (any part of "
        "them), or only those entirely inside it. By direction: dragging to the right "
        "selects what is inside, to the left what it touches (the box is dashed then).",
        choices=BOX_SELECTION,
        keywords=("selection", "rubber band", "crossing", "window", "drag"),
    ),
    Setting(
        "editor/path_width",
        2.0,
        "Default path width",
        "Editor",
        "Drawing",
        minimum=0.001,
        maximum=1e4,
        step=0.5,
        suffix=" µm",
        decimals=3,
    ),
)
BY_KEY = {s.key: s for s in SETTINGS}
LABEL_WIDTH = 170  # px: a setting's label, so the fields of every group line up

PAGES = {
    "Appearance": "eye",
    "Canvas": "grid",
    "Snapping": "snap",
    "Editor": "edit",
    "Keymap": "keyboard",
}


class Settings(QObject):
    """Typed access to the user's settings; ``changed(key)`` after every change."""

    changed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._store = QSettings(ORGANIZATION, APPLICATION)
        if self._store.contains("canvas/theme"):  # the setting's earlier name
            old = self._store.value("canvas/theme")
            self._store.remove("canvas/theme")
            if old in ("light", "dark"):
                self._store.setValue("appearance/canvas_theme", old)

    def get(self, key: str) -> Any:
        setting = BY_KEY[key]
        value = self._store.value(key, setting.default)
        return _convert(value, setting)

    def set(self, key: str, value: Any) -> None:
        setting = BY_KEY[key]
        value = _convert(value, setting)
        if value == self.get(key):
            return
        if value == setting.default:
            self._store.remove(key)
        else:
            self._store.setValue(key, value)
        self.changed.emit(key)

    def reset(self, keys: list[str] | None = None) -> None:
        for key in keys or list(BY_KEY):
            self.set(key, BY_KEY[key].default)

    # plain QSettings values that are not preferences (last folder, active tool)
    def value(self, key: str, default: Any = None) -> Any:
        return self._store.value(key, default)

    def set_value(self, key: str, value: Any) -> None:
        self._store.setValue(key, value)

    def remove_value(self, key: str) -> None:
        self._store.remove(key)


def _convert(value: Any, setting: Setting) -> Any:
    default = setting.default
    try:
        if isinstance(default, bool):
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes")
            return bool(value)
        if isinstance(default, int):
            value = int(float(value))
        elif isinstance(default, float):
            value = float(value)
        else:
            value = str(value)
    except (TypeError, ValueError):
        return default
    if setting.choices is not None and value not in setting.options():
        return default
    if setting.minimum is not None and value < setting.minimum:
        return setting.minimum if not isinstance(default, int) else int(setting.minimum)
    if setting.maximum is not None and value > setting.maximum:
        return setting.maximum if not isinstance(default, int) else int(setting.maximum)
    return value


class TrimmedSpinBox(QDoubleSpinBox):
    """A number field that shows 2 µm, not 2.000 µm: its decimals are the most it
    keeps, not how many it always shows."""

    def textFromValue(self, value: float) -> str:
        text = self.locale().toString(value, "f", self.decimals())
        if not self.isGroupSeparatorShown():
            text = text.replace(self.locale().groupSeparator(), "")
        point = self.locale().decimalPoint()
        if point in text:
            text = text.rstrip("0").removesuffix(point)
        return text


class PreferencesDialog(QDialog):
    """Settings by page, with a search field, like an IDE's settings dialog.

    Changes apply immediately; "Reset page" restores a page's defaults.
    ``shortcuts`` are ``(command, keys)`` or ``(command, keys, menu)`` for the
    Keymap page.
    """

    def __init__(self, settings: Settings, shortcuts: list[tuple[str, ...]], parent=None) -> None:
        super().__init__(parent)
        theme.load()  # themes added to the folder since the window opened
        self.settings = settings
        self.setWindowTitle("Settings")
        self.resize(860, 620)  # every page fits without scrolling (#318)
        self._editors: dict[str, QWidget] = {}
        self._rows: dict[str, tuple[QWidget, ...]] = {}
        self._headings: dict[str, QLabel] = {}  # by "page/group"

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search settings")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icons.icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._filter)
        self.side = widgets.SideList(
            [(icon_name, page) for page, icon_name in PAGES.items()], top=self.search
        )
        self.pages = self.side.list
        self.stack = QStackedWidget()
        for page in PAGES:
            if page == "Keymap":
                self.stack.addWidget(self._keymap_page(shortcuts))
            else:
                self.stack.addWidget(self._page(page))
        self.pages.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.pages.setCurrentRow(0)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Close).setDefault(True)
        reset = QPushButton("Reset page")
        reset.setAutoDefault(False)
        reset.setToolTip("Restore this page's defaults")
        reset.clicked.connect(self._reset_page)
        buttons.addButton(reset, QDialogButtonBox.ButtonRole.ResetRole)

        def show_reset(row: int) -> None:  # a page with nothing to reset has no Reset (#327)
            item = self.pages.item(row)
            page = item.text() if item is not None else ""
            reset.setVisible(any(setting.page == page for setting in SETTINGS))

        self.pages.currentRowChanged.connect(show_reset)
        show_reset(self.pages.currentRow())
        right = QVBoxLayout()
        pad = theme.metric("space_xl")
        right.setContentsMargins(0, 0, pad, pad)
        right.setSpacing(theme.metric("space_m"))
        right.addWidget(self.stack, 1)
        buttons_row = QHBoxLayout()
        buttons_row.setContentsMargins(pad, 0, 0, 0)
        buttons_row.addWidget(buttons)
        right.addLayout(buttons_row)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.side)
        layout.addLayout(right, 1)
        settings.changed.connect(self._show)

    def _scrolling(self, title: str) -> tuple[QScrollArea, QVBoxLayout]:
        """A page: its title, then what is added to the returned column; it scrolls
        when it is taller than the dialog."""
        content = QWidget()
        content.setObjectName("dialog-page")
        column = QVBoxLayout(content)
        pad = theme.metric("space_xl")
        column.setContentsMargins(pad, theme.metric("space_l"), pad, 0)
        column.setSpacing(theme.metric("space_s"))
        heading = QLabel(title)
        heading.setObjectName("heading")
        column.addWidget(heading)
        area = QScrollArea()
        area.setObjectName("dialog-page")
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(content)
        return area, column

    def _page(self, page: str) -> QWidget:
        area, outer = self._scrolling(page)
        row_height = theme.metric("row_height") + theme.metric("space_s")  # every row alike
        groups: dict[str, QFormLayout] = {}
        for setting in (s for s in SETTINGS if s.page == page):
            if setting.group not in groups:
                heading = self._group_heading(setting.group)
                self._headings[f"{page}/{setting.group}"] = heading
                outer.addWidget(heading)
                form = widgets.form()
                form.setVerticalSpacing(theme.metric("space_xs"))
                outer.addLayout(form)
                groups[setting.group] = form
            editor = self._editor(setting)
            shown = widgets.with_help(editor, setting.help) if setting.help else editor
            shown.setMinimumHeight(row_height)
            if isinstance(editor, QCheckBox):  # the check box carries its label
                groups[setting.group].addRow(shown)
                self._rows[setting.key] = (shown,)
                continue
            label = QLabel(setting.label)
            label.setFixedWidth(LABEL_WIDTH)  # the same for every group, so the fields line up
            groups[setting.group].addRow(label, shown)
            self._rows[setting.key] = (label, shown)
        if page == "Appearance":
            outer.addWidget(self._group_heading("Your themes"))
            outer.addWidget(self._themes_folder())
        outer.addStretch(1)
        return area

    @staticmethod
    def _group_heading(text: str) -> QLabel:
        heading = QLabel(text)
        heading.setObjectName("group-heading")
        heading.setContentsMargins(0, theme.metric("space_l"), 0, theme.metric("space_xs"))
        return heading

    def _themes_folder(self) -> QWidget:
        """Where the user's themes go, and why a theme found there could not be used."""
        widget = QWidget()
        column = QVBoxLayout(widget)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.metric("space_s"))
        line = QHBoxLayout()
        line.setSpacing(theme.metric("space_m"))
        folder = theme.user_folder()
        # A long path is cut in the middle, not off the dialog's edge (#260).
        label = widgets.ElidedLabel(str(folder), mode=Qt.TextElideMode.ElideMiddle)
        label.setObjectName("muted")
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        line.addWidget(label, 1)
        button = QPushButton("Open folder")
        button.setAutoDefault(False)
        button.setToolTip("Put a theme's .json file here; it shows in Interface theme")
        button.clicked.connect(lambda: open_folder(folder))
        line.addWidget(button)
        column.addLayout(line)
        for problem in theme.problems():
            text = QLabel(problem)
            text.setObjectName("muted")
            text.setProperty("error", True)
            text.setWordWrap(True)
            column.addWidget(text)
        return widget

    def _editor(self, setting: Setting) -> QWidget:
        key, value = setting.key, self.settings.get(setting.key)
        if isinstance(setting.default, bool):
            editor = QCheckBox(setting.label)
            editor.setChecked(value)
            editor.toggled.connect(lambda v, k=key: self.settings.set(k, v))
        elif setting.choices is not None:
            editor = QComboBox()
            for choice, text in setting.options().items():
                editor.addItem(text, choice)
            editor.setCurrentIndex(editor.findData(value))
            editor.currentIndexChanged.connect(
                lambda _i, k=key, e=editor: self.settings.set(k, e.currentData())
            )
        elif isinstance(setting.default, int):
            editor = QSpinBox()
            editor.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            editor.setRange(int(setting.minimum), int(setting.maximum))
            editor.setSingleStep(int(setting.step or 1))
            editor.setSuffix(setting.suffix)
            editor.setValue(value)
            editor.valueChanged.connect(lambda v, k=key: self.settings.set(k, v))
        else:
            editor = TrimmedSpinBox()
            editor.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
            editor.setDecimals(setting.decimals)
            editor.setRange(setting.minimum, setting.maximum)
            editor.setSingleStep(setting.step or 1)
            editor.setSuffix(setting.suffix)
            if setting.special:
                editor.setSpecialValueText(setting.special)
            editor.setValue(value)
            editor.valueChanged.connect(lambda v, k=key: self.settings.set(k, v))
        if not isinstance(editor, QCheckBox):
            editor.setFixedWidth(200)
        self._editors[key] = editor
        return editor

    def _keymap_page(self, shortcuts: list[tuple[str, ...]]) -> QWidget:
        widget = QWidget()
        widget.setObjectName("dialog-page")
        layout = QVBoxLayout(widget)
        pad = theme.metric("space_xl")
        layout.setContentsMargins(pad, theme.metric("space_l"), pad, 0)
        layout.setSpacing(theme.metric("space_m"))
        title = QLabel("Keymap")
        title.setObjectName("heading")
        layout.addWidget(title)
        rows: list[tuple[str, str, str]] = []
        for name, keys, *menu in shortcuts:
            row = (name, keys, menu[0] if menu else "")
            if all(row[:2] != seen[:2] for seen in rows):  # a command in two menus once
                rows.append(row)
        self.keymap = widgets.make_table(("Command", "Shortcut", "Menu"), stretch=0)
        self.keymap.setObjectName("keymap")
        self.keymap.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.keymap.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.keymap.setRowCount(len(rows))
        muted = theme.color("muted")
        for r, (name, keys, menu) in enumerate(rows):
            self.keymap.setItem(r, 0, QTableWidgetItem(name))
            self.keymap.setItem(r, 1, QTableWidgetItem(keys))
            where = QTableWidgetItem(menu)
            where.setForeground(muted)
            self.keymap.setItem(r, 2, where)
            self.keymap.item(r, 0).setData(
                Qt.ItemDataRole.UserRole, f"{name} {keys} {menu}".lower()
            )
        self._keymap_empty = widgets.placeholder(self.keymap, "")
        layout.addWidget(self.keymap, 1)
        note = QLabel("Mouse: the wheel zooms; middle or right drag, or Space + drag, pans.")
        note.setObjectName("muted")
        layout.addWidget(note)
        return widget

    def _show(self, key: str) -> None:
        """Keep an editor in step when a setting changes from elsewhere."""
        editor = self._editors.get(key)
        if editor is None:
            return
        value = self.settings.get(key)
        editor.blockSignals(True)
        if isinstance(editor, QCheckBox):
            editor.setChecked(value)
        elif isinstance(editor, QComboBox):
            editor.setCurrentIndex(editor.findData(value))
        else:
            editor.setValue(value)
        editor.blockSignals(False)

    def _reset_page(self) -> None:
        page = self.pages.currentItem().text()
        self.settings.reset([s.key for s in SETTINGS if s.page == page])

    def _filter(self, text: str) -> None:
        """Show only matching settings; jump to the first page with a match."""
        words = text.lower().split()
        first = None
        groups_shown: set[str] = set()
        for setting in SETTINGS:
            haystack = " ".join(
                (setting.label, setting.group, setting.page, setting.help, *setting.keywords)
            ).lower()
            shown = all(w in haystack for w in words)
            for widget in self._rows[setting.key]:
                widget.setVisible(shown)
            if shown:
                groups_shown.add(f"{setting.page}/{setting.group}")
                first = first or setting.page
        for key, heading in self._headings.items():
            heading.setVisible(key in groups_shown)
        keys_shown = 0
        for row in range(self.keymap.rowCount()):
            haystack = self.keymap.item(row, 0).data(Qt.ItemDataRole.UserRole)
            hidden = not all(w in haystack for w in words)
            self.keymap.setRowHidden(row, hidden)
            keys_shown += not hidden
        self._keymap_empty.setText(f"No shortcut matches “{text.strip()}”.")
        self._keymap_empty.refresh()
        if words and first is None and keys_shown:
            first = "Keymap"
        if words and first is not None:
            self.pages.setCurrentRow(list(PAGES).index(first))


def open_folder(folder) -> None:
    """Show ``folder`` in the system's file manager (made first if missing)."""
    folder.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
