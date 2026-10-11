"""The look of the application: a light and a dark theme after JetBrains' Islands.

The window is a *frame* (toolbar, tool window stripes, the gaps between
panels, status bar) holding *islands*: every tool window and the editor, with
rounded corners and no border lines between them.

A theme is a JSON file of colours in three groups: ``ui`` (the window, filled
into the style sheet below), ``canvas`` (the layout canvas) and ``icons``.
The built-in ``light`` and ``dark`` themes are in ``gui/themes/``. More come
from packages, through the ``mems_sketch.themes`` entry-point group, and from
the user's themes folder (:func:`user_folder`). A theme can name a ``parent``
and give only the colours it changes. A theme sets colours only: the style
sheet stays this module's, so a theme cannot break the layout.

``apply(app, name)`` sets the Qt palette, the style sheet and the icon colours
for a theme, or ``system`` (light or dark, following the desktop).
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from mems_sketch.gui import icons

HEADER_HEIGHT = 32  # tool window headers and tab bars share this height, so edges line up
ISLAND_RADIUS = 10  # px: the corners of the islands (and of the canvas in the editor's)
SYSTEM = "system"  # follow the desktop: the built-in light or dark theme
BUILT_IN = Path(__file__).with_name("themes")
ENTRY_POINT_GROUP = "mems_sketch.themes"
log = logging.getLogger(__name__)

_COLOR = re.compile(r"^#([0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def _color(value: str) -> str:
    if not isinstance(value, str) or not _COLOR.match(value):
        raise ValueError(f"{value!r} is not a colour: write #rrggbb or #aarrggbb")
    return value


class _Colors(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("*", mode="before")
    @classmethod
    def _colors(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [_color(v) if isinstance(v, str) else v for v in value]
        return _color(value) if isinstance(value, str) or value is None else value


class UiColors(_Colors):
    """The window's colours, filled into the style sheet."""

    frame: str  # toolbar, stripes, the gaps between islands, status bar
    island: str  # tool windows and the editor
    window: str  # dialogs
    editor: str  # the editor area and inputs
    border: str
    border_strong: str
    text: str
    muted: str  # secondary text
    hover: str
    pressed: str
    selected: str
    selected_inactive: str
    accent: str
    accent_text: str
    input_border: str
    control: str  # drop-down lists: set apart from the fields and the island
    tooltip: str
    scroll: str
    expression: str  # a field holding an expression (a parameter's purple, light)
    error: str
    warning: str


class CanvasColors(_Colors):
    """The layout canvas's colours."""

    background: str
    grid: str  # grid lines: this colour at the opacities of grid_alpha
    grid_alpha: tuple[int, int, int]  # minor lines, every fifth line, the axes (0-255)
    highlight: str  # the selection
    hover: str
    violation: str  # rule violations
    added: str  # history: material a version added
    removed: str  # and removed (hatched: it is not there any more)
    declared: str  # points: the edited component's own
    selected: str  # of the selected shape
    pick: str  # candidates while aligning
    snap: str  # the point a drag snaps to
    anchor: str  # a point a tool has fixed
    focus: str  # hovered or selected in the Points panel
    ruler: str
    guide: str
    axis_x: str
    axis_y: str
    gizmo_free: str
    gizmo_ring: str
    overlay: str  # overlay text
    overlay_muted: str
    layers: list[str] = Field(min_length=1)  # layers without a colour of their own, in turn
    unknown_layer: str  # a layer the process does not define

    @field_validator("grid_alpha")
    @classmethod
    def _alpha(cls, value: tuple[int, int, int]) -> tuple[int, int, int]:
        if not all(0 <= a <= 255 for a in value):
            raise ValueError("opacities are 0 to 255")
        return value


class IconColors(_Colors):
    """The icons' colours: the outline (``fg``) and the accents."""

    fg: str
    blue: str
    red: str
    green: str
    orange: str
    yellow: str
    purple: str
    x: str  # the x axis
    y: str  # the y axis
    on_accent: str  # marks drawn on the coloured badges (error, info, ok)
    on_yellow: str  # and on the yellow one (warning)


GROUPS = {"ui": UiColors, "canvas": CanvasColors, "icons": IconColors}


class _File(BaseModel):
    """A theme file as written: a parent's colours fill in what it leaves out."""

    model_config = ConfigDict(extra="forbid")

    name: str
    parent: str | None = None
    dark: bool | None = None
    ui: dict[str, Any] = {}
    canvas: dict[str, Any] = {}
    icons: dict[str, Any] = {}


@dataclass(frozen=True)
class Theme:
    id: str
    name: str
    dark: bool
    ui: dict[str, str]
    canvas: dict[str, Any]
    icons: dict[str, str]
    source: str  # where it was found, for messages


class ThemeError(ValueError):
    pass


_themes: dict[str, Theme] | None = None
_problems: list[str] = []
_current: Theme | None = None
_applied: dict[int, Theme] = {}  # per application: the theme its style sheet has


def user_folder() -> Path:
    """Where the user's own themes go, one ``<id>.json`` each."""
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericConfigLocation)
    return Path(base) / "mems-sketch" / "themes"


def _sources() -> list[tuple[str, Any, str]]:
    """(id, raw theme or path, where) for every theme, built-in ones first."""
    found: list[tuple[str, Any, str]] = [
        (path.stem, path, "built in") for path in sorted(BUILT_IN.glob("*.json"))
    ]
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        where = f"package {ep.value}"
        try:
            value = ep.load()
            found.append((ep.name, value() if callable(value) else value, where))
        except Exception as e:  # noqa: BLE001 - a broken plugin must not stop the window
            found.append((ep.name, ThemeError(f"could not be loaded: {e}"), where))
    folder = user_folder()
    if folder.is_dir():
        found += [(path.stem, path, str(path)) for path in sorted(folder.glob("*.json"))]
    return found


def _read(raw: Any) -> _File:
    if isinstance(raw, Exception):
        raise raw
    if isinstance(raw, (str, Path)):
        try:
            raw = json.loads(Path(raw).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ThemeError(f"could not be read: {e}") from e
    try:
        return _File.model_validate(raw)
    except ValidationError as e:
        raise ThemeError(_explain(e)) from e


def _explain(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in item['loc']) or 'the file'}: {item['msg']}"
        for item in error.errors()
    )


def _resolve_all(files: dict[str, tuple[_File, str]]) -> dict[str, Theme]:
    done: dict[str, Theme] = {}

    def build(tid: str) -> Theme:
        if tid in done:
            return done[tid]
        file, where = files[tid]
        seen, up = [tid], file.parent
        while up is not None and up in files:
            if up in seen:
                raise ThemeError(f"its parents go round in a circle: {' → '.join([*seen, up])}")
            seen.append(up)
            up = files[up][0].parent
        merged: dict[str, dict[str, Any]] = {"ui": {}, "canvas": {}, "icons": {}}
        dark = file.dark
        if file.parent is not None:
            if file.parent not in files:
                raise ThemeError(f"its parent {file.parent!r} is not a theme")
            try:
                parent = build(file.parent)
            except ThemeError as e:
                raise ThemeError(f"its parent {file.parent!r} cannot be used") from e
            merged = {
                "ui": dict(parent.ui),
                "canvas": dict(parent.canvas),
                "icons": dict(parent.icons),
            }
            dark = parent.dark if dark is None else dark
        for group in merged:
            merged[group].update(getattr(file, group))
        colors = {}
        for group, model in GROUPS.items():
            try:
                colors[group] = model.model_validate(merged[group]).model_dump()
            except ValidationError as e:
                raise ThemeError(f"{group}: {_explain(e)}") from e
        done[tid] = Theme(tid, file.name, bool(dark), **colors, source=where)
        return done[tid]

    themes: dict[str, Theme] = {}
    for tid, (_file, where) in files.items():
        try:
            themes[tid] = build(tid)
        except ThemeError as e:
            _problems.append(f"Theme {tid!r} ({where}): {e}")
    return themes


def load() -> dict[str, Theme]:
    """Find every theme again (the user's folder may have changed); id -> theme."""
    global _themes
    _problems.clear()
    files: dict[str, tuple[_File, str]] = {}
    for tid, raw, where in _sources():
        if tid == SYSTEM or tid in files:
            _problems.append(f"Theme {tid!r} ({where}): that name is taken")
            continue
        try:
            files[tid] = (_read(raw), where)
        except ThemeError as e:
            _problems.append(f"Theme {tid!r} ({where}): {e}")
    _themes = _resolve_all(files)
    for problem in _problems:
        log.warning(problem)
    return _themes


def themes() -> dict[str, Theme]:
    return _themes if _themes is not None else load()


def problems() -> list[str]:
    """Why themes that were found could not be used."""
    themes()
    return list(_problems)


def choices() -> dict[str, str]:
    """The interface theme setting's choices: value -> label."""
    return {SYSTEM: "Same as the system"} | {t.id: t.name for t in themes().values()}


def resolve(name: str) -> str:
    """A theme's id: ``system`` (or a theme that is gone) follows the desktop."""
    if name in themes():
        return name
    hints = QGuiApplication.styleHints()
    scheme = hints.colorScheme() if hints is not None else Qt.ColorScheme.Unknown
    return "dark" if scheme == Qt.ColorScheme.Dark else "light"


def get(name: str) -> Theme:
    return themes()[resolve(name)]


def tokens(name: str) -> dict[str, str]:
    """The window colours of a theme."""
    return get(name).ui


def current() -> Theme:
    """The theme the window has (light before :func:`apply`)."""
    return _current if _current is not None else get("light")


def color(key: str) -> QColor:
    """A window colour of the current theme (``muted``, ``warning``, ...)."""
    return QColor(current().ui[key])


STYLE = """
QMainWindow {{ background: {frame}; }}
QDialog {{ background: {window}; }}
QWidget {{ color: {text}; }}
QMainWindow::separator {{ background: {frame}; width: 5px; height: 5px; }}

QWidget#tool-windows, QWidget#tool-window-stripe-left, QWidget#tool-window-stripe-right {{
    background: {frame};
}}
QWidget#island {{ background: {island}; border-radius: {radius}px; }}

QToolBar {{ background: {frame}; border: none; spacing: 2px; padding: 3px 6px; }}
QToolBar::separator {{ background: {border_strong}; width: 1px; height: 1px; margin: 4px 5px; }}
QToolButton {{
    background: transparent; border: none; border-radius: 5px; padding: 4px;
}}
QToolButton:hover {{ background: {hover}; }}
QToolButton:pressed {{ background: {pressed}; }}
QToolButton:checked {{ background: {selected}; }}
QToolButton[popupMode="2"] {{ padding-right: 12px; }}
QToolButton#main-menu {{ padding: 5px; }}
QToolButton::menu-indicator {{ image: none; }}
QToolBar#tools-toolbar QToolButton {{ padding: 5px; margin: 1px 2px; }}

QMenuBar {{ background: {window}; }}
QMenuBar::item {{ padding: 4px 8px; border-radius: 4px; }}
QMenuBar::item:selected {{ background: {hover}; }}
QMenu {{
    background: {editor}; border: 1px solid {border_strong}; border-radius: 8px;
    padding: 5px;
}}
QMenu::item {{ padding: 5px 24px 5px 8px; border-radius: 4px; }}
QMenu::item:selected {{ background: {selected}; }}
QMenu::item:disabled {{ color: {muted}; }}
QMenu::separator {{ height: 1px; background: {border_strong}; margin: 4px 6px; }}
QMenu::icon {{ padding-left: 6px; }}

QDockWidget {{ titlebar-close-icon: none; titlebar-normal-icon: none; }}
QDockWidget::title {{ background: {window}; padding: 0 10px; text-align: left; }}
QWidget#dock-title {{ background: transparent; }}
QLabel#dock-title-label {{ font-weight: 600; }}
QDockWidget > QWidget {{ background: {window}; }}

QTabWidget::pane {{ border: none; }}
QTabWidget::tab-bar {{ left: 6px; }}
QTabBar {{ background: transparent; qproperty-drawBase: 0; }}  /* the island shows, rounded corners too */
QTabBar::tab {{
    background: transparent; color: {text}; height: 22px; padding: 2px 10px; margin: 3px 2px;
    border: 1px solid transparent; border-radius: 6px;
}}
QTabBar::tab:selected {{ background: {selected_inactive}; border: 1px solid {border_strong}; }}
QTabBar::tab:hover:!selected {{ background: {hover}; }}
QToolButton#tab-close {{ padding: 1px; border-radius: 3px; }}

QTreeView, QTableView, QListView, QTableWidget, QTreeWidget, QListWidget {{
    background: {island}; alternate-background-color: {island}; border: none;
    selection-background-color: {selected}; selection-color: {text};
    gridline-color: {border};
}}
QTreeView::item, QListView::item {{ padding: 2px 0; }}
QTreeView::item:hover, QListView::item:hover {{ background: {hover}; }}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{
    background: {selected}; color: {text};
}}
QHeaderView::section {{
    background: {island}; color: {muted}; border: none; padding: 4px 6px;
}}
QTableView::item {{ padding: 0 4px; }}
QTableCornerButton::section {{ background: {island}; border: none; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {editor}; border: 1px solid {border_strong}; border-radius: 4px;
    padding: 3px 6px; selection-background-color: {selected}; selection-color: {text};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus {{ border: 1px solid {accent}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {muted}; background: {window};
}}
QComboBox {{ background: {control}; border: 1px solid {input_border}; padding-right: 22px; }}
QComboBox:hover {{ background: {hover}; }}
QComboBox:editable {{ background: {editor}; }}
QComboBox QLineEdit {{ background: transparent; border: none; padding: 0; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{ image: url({arrow}); width: 10px; height: 10px; }}
QComboBox::down-arrow:disabled {{ image: url({arrow_disabled}); }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0; border: none; }}
QComboBox QAbstractItemView {{
    background: {editor}; border: 1px solid {border_strong}; selection-background-color: {selected};
}}

QPushButton {{
    background: {editor}; border: 1px solid {input_border}; border-radius: 4px;
    padding: 4px 12px;
}}
QPushButton:hover {{ background: {hover}; }}
QPushButton:pressed {{ background: {pressed}; }}
QPushButton:default {{ background: {accent}; color: {accent_text}; border-color: {accent}; }}
QPushButton:disabled {{ color: {muted}; }}

QCheckBox, QRadioButton {{ spacing: 6px; }}
QScrollArea {{ background: {island}; border: none; }}
QWidget#properties-body {{ background: {island}; }}
QGroupBox#section {{
    border: none; border-top: 1px solid {border_strong}; margin-top: 18px; padding-top: 8px;
    font-weight: 600;
}}
QGroupBox#section::title {{ subcontrol-origin: margin; left: 0; top: 6px; padding: 0; }}
QGroupBox#section::indicator {{
    width: 13px; height: 13px; border: 1px solid {input_border}; border-radius: 3px;
    background: {editor};
}}
QGroupBox#section::indicator:checked {{
    background: {accent}; border-color: {accent}; image: url({check});
}}
QCheckBox::indicator, QTreeView::indicator, QTableView::indicator {{
    width: 13px; height: 13px; border: 1px solid {input_border}; border-radius: 3px;
    background: {editor};
}}
QCheckBox::indicator:checked, QTreeView::indicator:checked, QTableView::indicator:checked {{
    background: {accent}; border-color: {accent}; image: url({check});
}}
QCheckBox::indicator:disabled {{ background: {window}; }}

QStatusBar {{ background: {frame}; border: none; color: {muted}; min-height: 26px; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {muted}; padding: 0 6px; }}
QStatusBar QToolButton {{ padding: 2px 5px; }}
QStatusBar QComboBox, QStatusBar QDoubleSpinBox {{ padding: 0 6px; }}
QGraphicsView {{ border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle {{ background: {scroll}; border-radius: 4px; min-height: 24px; min-width: 24px;
    margin: 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: {frame}; }}
QToolTip {{
    background: {tooltip}; color: {text}; border: 1px solid {border_strong}; padding: 4px 6px;
    border-radius: 4px;
}}

QLineEdit[expression="true"] {{ background: {expression}; }}
QLineEdit[invalid="true"] {{ border: 1px solid {error}; }}
QLineEdit#title-edit {{
    background: transparent; border: 1px solid transparent; font-size: 14px; font-weight: 600;
    padding: 2px 4px;
}}
QLineEdit#title-edit:hover {{ border: 1px solid {border_strong}; }}
QLineEdit#title-edit:focus {{ border: 1px solid {accent}; background: {editor}; }}
QFrame#modifier-card {{
    background: {editor}; border: 1px solid {border_strong}; border-radius: 6px;
}}
QFrame#modifier-card[off="true"] QLabel, QFrame#modifier-card[off="true"] QLineEdit {{
    color: {muted};
}}
QLabel#card-title {{ font-weight: 600; }}
QWidget#canvas-buttons {{
    background: {editor}; border: 1px solid {border_strong}; border-radius: 6px;
}}
QLabel#muted {{ color: {muted}; }}
QLabel#muted[error="true"] {{ color: {error}; }}
QLabel#heading {{ font-weight: 600; font-size: 13px; }}
QWidget#view-header {{ background: {editor}; border-bottom: 1px solid {border}; }}
QWidget#view-header QLabel {{ color: {muted}; }}
QWidget#view-header QLabel#crumb-current {{ color: {text}; font-weight: 600; }}
QListWidget#settings-pages {{ background: {window}; border-right: 1px solid {border}; }}
QListWidget#settings-pages::item {{ border-radius: 4px; padding-left: 6px; }}
QDialog#find-action {{ background: {editor}; border: 1px solid {border_strong}; }}
QTreeWidget#command-list {{ background: {editor}; }}
QListWidget#command-list {{ background: {editor}; }}
QListWidget#command-list::item {{ padding: 5px 8px; border-radius: 4px; }}
"""


def palette(theme: str) -> QPalette:
    t = get(theme).ui
    p = QPalette()
    roles = QPalette.ColorRole
    for role, key in (
        (roles.Window, "window"),
        (roles.WindowText, "text"),
        (roles.Base, "editor"),
        (roles.AlternateBase, "window"),
        (roles.ToolTipBase, "tooltip"),
        (roles.ToolTipText, "text"),
        (roles.Text, "text"),
        (roles.Button, "window"),
        (roles.ButtonText, "text"),
        (roles.Highlight, "selected"),
        (roles.HighlightedText, "text"),
        (roles.Link, "accent"),
        (roles.PlaceholderText, "muted"),
        (roles.Mid, "border_strong"),
        (roles.Midlight, "border"),
        (roles.Dark, "input_border"),
    ):
        p.setColor(role, QColor(t[key]))
    for role in (roles.Text, roles.WindowText, roles.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(t["muted"]))
    return p


def apply(app: QApplication, name: str) -> str:
    """Give the application the theme ``name``; returns the id of the theme it got."""
    global _current
    theme = resolve(name)
    _current = themes()[theme]
    icons.set_theme(theme)
    if app.property("mems_sketch_theme") == theme and _applied.get(id(app)) is _current:
        return theme  # re-polishing every widget is slow; nothing would change
    app.setProperty("mems_sketch_theme", theme)
    _applied[id(app)] = _current
    app.setStyle("Fusion")
    app.setPalette(palette(theme))
    t = _current.ui
    app.setStyleSheet(
        STYLE.format(
            **t,
            check=_svg_file(f"check-{theme}", CHECK_MARK, t["accent_text"]),
            arrow=_svg_file(f"arrow-{theme}", CHEVRON, t["text"]),
            arrow_disabled=_svg_file(f"arrow-{theme}-disabled", CHEVRON, t["muted"]),
            radius=ISLAND_RADIUS,
        )
    )
    return theme


CHECK_MARK = '<path d="M3.5 8.4l3 3 6-6.4" stroke-width="2"/>'  # check boxes
CHEVRON = '<path d="M4 6l4 4 4-4" stroke-width="1.5"/>'  # drop-down lists


def _svg_file(name: str, shape: str, color: str) -> str:
    """``shape`` drawn in ``color`` as an SVG file, for the style sheet (which needs a file)."""
    path = Path(tempfile.gettempdir()) / f"mems-sketch-{name}.svg"
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" '
        f'stroke="{color}" stroke-linecap="round" stroke-linejoin="round">{shape}</svg>'
    )
    try:
        if not path.exists() or path.read_text() != svg:
            path.write_text(svg)
    except OSError:
        return ""
    return path.as_posix()
