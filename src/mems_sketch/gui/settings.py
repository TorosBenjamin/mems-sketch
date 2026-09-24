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

from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QObject, QSettings, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.gui import icons
from mems_sketch.gui.help import HelpButton
from mems_sketch.gui.theme import UI_THEMES

ORGANIZATION = APPLICATION = "mems-sketch"
CANVAS_THEMES = {"auto": "Same as the interface", "light": "Light", "dark": "Dark"}


@dataclass(frozen=True)
class Setting:
    key: str
    default: Any
    label: str
    page: str
    group: str
    help: str = ""
    choices: dict[str, str] | None = None  # value -> label
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    suffix: str = ""
    decimals: int = 0
    keywords: tuple[str, ...] = field(default_factory=tuple)


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
        choices=UI_THEMES,
        keywords=("dark", "light", "colour", "color"),
    ),
    Setting(
        "appearance/canvas_theme",
        "auto",
        "Canvas background",
        "Appearance",
        "Theme",
        "The layout canvas can stay light in a dark interface, or the other way round.",
        choices=CANVAS_THEMES,
        keywords=("dark", "white", "background"),
    ),
    # -- Canvas -------------------------------------------------------------
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
        "Draw without smoothing during a zoom or resize, then in full quality once it "
        "stops: much faster on big designs.",
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
        "Show how library and built-in components are built",
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
PAGES = {
    "Appearance": "eye",
    "Canvas": "grid",
    "Snapping": "snap",
    "Editor": "edit",
    "Keymap": "search",
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
    if setting.choices is not None and value not in setting.choices:
        return default
    if setting.minimum is not None and value < setting.minimum:
        return setting.minimum if not isinstance(default, int) else int(setting.minimum)
    if setting.maximum is not None and value > setting.maximum:
        return setting.maximum if not isinstance(default, int) else int(setting.maximum)
    return value


class PreferencesDialog(QDialog):
    """Settings by page, with a search field, like an IDE's settings dialog.

    Changes apply immediately; "Reset page" restores a page's defaults.
    """

    def __init__(self, settings: Settings, shortcuts: list[tuple[str, str]], parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Settings")
        self.resize(760, 520)
        self._editors: dict[str, QWidget] = {}
        self._rows: dict[str, tuple[QWidget, ...]] = {}
        self._headings: dict[str, QLabel] = {}  # by "page/group"

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search settings")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icons.icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._filter)
        self.pages = QListWidget()
        self.pages.setObjectName("settings-pages")
        self.pages.setFixedWidth(180)
        self.stack = QStackedWidget()
        self.pages.setIconSize(QSize(18, 18))
        self.pages.setSpacing(1)
        for page, icon_name in PAGES.items():
            item = QListWidgetItem(icons.icon(icon_name), page)
            item.setSizeHint(QSize(0, 30))  # the highlight stays inside its row
            self.pages.addItem(item)
            if page == "Keymap":
                self.stack.addWidget(self._keymap_page(shortcuts))
            else:
                self.stack.addWidget(self._page(page))
        self.pages.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.pages.setCurrentRow(0)

        left = QVBoxLayout()
        left.addWidget(self.search)
        left.addWidget(self.pages)
        body = QHBoxLayout()
        body.addLayout(left)
        body.addWidget(self.stack, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Close).setDefault(True)
        reset = QPushButton("Reset page")
        reset.setAutoDefault(False)
        reset.setToolTip("Restore this page's defaults")
        reset.clicked.connect(self._reset_page)
        buttons.addButton(reset, QDialogButtonBox.ButtonRole.ResetRole)
        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addWidget(buttons)
        settings.changed.connect(self._show)

    def _page(self, page: str) -> QWidget:
        widget = QWidget()
        outer = QVBoxLayout(widget)
        outer.setContentsMargins(16, 4, 8, 4)
        title = QLabel(page)
        title.setObjectName("heading")
        outer.addWidget(title)
        groups: dict[str, QFormLayout] = {}
        for setting in (s for s in SETTINGS if s.page == page):
            if setting.group not in groups:
                heading = QLabel(setting.group)
                heading.setObjectName("muted")
                self._headings[f"{page}/{setting.group}"] = heading
                outer.addSpacing(8)
                outer.addWidget(heading)
                form = QFormLayout()
                form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
                form.setHorizontalSpacing(16)
                outer.addLayout(form)
                groups[setting.group] = form
            editor = self._editor(setting)
            shown = editor
            if setting.help:  # a "?" after it explains it
                shown = QWidget()
                line = QHBoxLayout(shown)
                line.setContentsMargins(0, 0, 0, 0)
                line.setSpacing(4)
                line.addWidget(editor)
                line.addWidget(HelpButton(setting.help))
                line.addStretch(1)
            if isinstance(editor, QCheckBox):  # the check box carries its label
                groups[setting.group].addRow(shown)
                self._rows[setting.key] = (shown,)
                continue
            label = QLabel(setting.label)
            label.setFixedWidth(170)  # the same for every group, so the fields line up
            groups[setting.group].addRow(label, shown)
            self._rows[setting.key] = (label, shown)
        outer.addStretch(1)
        return widget

    def _editor(self, setting: Setting) -> QWidget:
        key, value = setting.key, self.settings.get(setting.key)
        if isinstance(setting.default, bool):
            editor = QCheckBox(setting.label)
            editor.setChecked(value)
            editor.toggled.connect(lambda v, k=key: self.settings.set(k, v))
        elif setting.choices is not None:
            editor = QComboBox()
            for choice, text in setting.choices.items():
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
            editor = QDoubleSpinBox()
            editor.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
            editor.setDecimals(setting.decimals)
            editor.setRange(setting.minimum, setting.maximum)
            editor.setSingleStep(setting.step or 1)
            editor.setSuffix(setting.suffix)
            editor.setValue(value)
            editor.valueChanged.connect(lambda v, k=key: self.settings.set(k, v))
        if not isinstance(editor, QCheckBox):
            editor.setFixedWidth(200)
        self._editors[key] = editor
        return editor

    def _keymap_page(self, shortcuts: list[tuple[str, str]]) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(16, 4, 8, 4)
        title = QLabel("Keymap")
        title.setObjectName("heading")
        layout.addWidget(title)
        self.keymap = QListWidget()
        for name, keys in shortcuts:
            item = QListWidgetItem(f"{name}\t{keys}")
            item.setData(Qt.ItemDataRole.UserRole, f"{name} {keys}".lower())
            self.keymap.addItem(item)
        layout.addWidget(self.keymap)
        note = QLabel("Mouse: wheel zooms, middle or right drag pans, Space + drag pans.")
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
        for row in range(self.keymap.count()):
            item = self.keymap.item(row)
            item.setHidden(not all(w in item.data(Qt.ItemDataRole.UserRole) for w in words))
        if words and first is not None:
            self.pages.setCurrentRow(list(PAGES).index(first))
