"""The look of the application: a light and a dark theme after JetBrains' Islands.

The window is a *frame* (toolbar, tool window stripes, the gaps between
panels, status bar) holding *islands*: every tool window and the editor, with
rounded corners and no border lines between them.

``apply(app, name)`` sets the Qt palette, a style sheet and the icon colours
for ``light``, ``dark`` or ``system`` (follow the desktop). The canvas has its
own colours (see :mod:`mems_sketch.gui.canvas`); by default it follows this.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from mems_sketch.gui import icons

HEADER_HEIGHT = 32  # tool window headers and tab bars share this height, so edges line up
UI_THEMES = {"system": "Same as the system", "light": "Light", "dark": "Dark"}

TOKENS = {
    "light": {
        "frame": "#ebecf0",  # toolbar, stripes, the gaps between islands, status bar
        "island": "#ffffff",  # tool windows and the editor
        "window": "#f7f8fa",  # dialogs
        "editor": "#ffffff",  # the editor area and inputs
        "border": "#ebecf0",
        "border_strong": "#dfe1e5",
        "text": "#1e1f22",
        "muted": "#818594",
        "hover": "#dfe1e5",
        "pressed": "#d3d5db",
        "selected": "#d4e2ff",
        "selected_inactive": "#dfe1e5",
        "accent": "#3574f0",
        "accent_text": "#ffffff",
        "input_border": "#c9ccd6",
        "tooltip": "#ffffff",
        "scroll": "#c9ccd6",
    },
    "dark": {
        "frame": "#26282c",  # measured from IntelliJ's dark Islands theme
        "island": "#191a1c",
        "window": "#2b2d30",
        "editor": "#1e1f22",
        "border": "#1e1f22",
        "border_strong": "#393b40",
        "text": "#dfe1e5",
        "muted": "#868a91",
        "hover": "#393b40",
        "pressed": "#43454a",
        "selected": "#2e436e",
        "selected_inactive": "#43454a",
        "accent": "#3574f0",
        "accent_text": "#ffffff",
        "input_border": "#4e5157",
        "tooltip": "#393b40",
        "scroll": "#4e5157",
    },
}

STYLE = """
QMainWindow {{ background: {frame}; }}
QDialog {{ background: {window}; }}
QWidget {{ color: {text}; }}
QMainWindow::separator {{ background: {frame}; width: 5px; height: 5px; }}

QWidget#tool-windows, QWidget#tool-window-stripe-left, QWidget#tool-window-stripe-right {{
    background: {frame};
}}
QWidget#island {{ background: {island}; border-radius: 10px; }}

QToolBar {{ background: {frame}; border: none; spacing: 2px; padding: 3px 6px; }}
QToolBar::separator {{ background: {border_strong}; width: 1px; height: 1px; margin: 4px 5px; }}
QToolButton {{
    background: transparent; border: none; border-radius: 5px; padding: 4px;
}}
QToolButton:hover {{ background: {hover}; }}
QToolButton:pressed {{ background: {pressed}; }}
QToolButton:checked {{ background: {selected}; }}
QToolButton[popupMode="2"] {{ padding-right: 12px; }}
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
QTabBar {{ background: {island}; qproperty-drawBase: 0; }}
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
QComboBox::drop-down {{ border: none; width: 18px; }}
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

QWidget#canvas-buttons {{
    background: {editor}; border: 1px solid {border_strong}; border-radius: 6px;
}}
QLabel#muted {{ color: {muted}; }}
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


def resolve(name: str) -> str:
    """``light`` or ``dark``: ``system`` is resolved from the desktop's colour scheme."""
    if name in ("light", "dark"):
        return name
    hints = QGuiApplication.styleHints()
    scheme = hints.colorScheme() if hints is not None else Qt.ColorScheme.Unknown
    return "dark" if scheme == Qt.ColorScheme.Dark else "light"


def tokens(name: str) -> dict[str, str]:
    return TOKENS[resolve(name)]


def palette(theme: str) -> QPalette:
    t = TOKENS[theme]
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
    """Give the application the theme ``name``; returns the resolved ``light``/``dark``."""
    theme = resolve(name)
    icons.set_theme(theme)
    if app.property("mems_sketch_theme") == theme:
        return theme  # re-polishing every widget is slow; nothing would change
    app.setProperty("mems_sketch_theme", theme)
    app.setStyle("Fusion")
    app.setPalette(palette(theme))
    app.setStyleSheet(STYLE.format(**TOKENS[theme], check=_check_mark()))
    return theme


def _check_mark() -> str:
    """A white check mark as an SVG file, for the style sheet's check boxes."""
    path = Path(tempfile.gettempdir()) / "mems-sketch-check.svg"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M3.5 8.4l3 3 '
        '6-6.4" fill="none" stroke="#ffffff" stroke-width="2" stroke-linecap="round" '
        'stroke-linejoin="round"/></svg>'
    )
    try:
        if not path.exists() or path.read_text() != svg:
            path.write_text(svg)
    except OSError:
        return ""
    return path.as_posix()
