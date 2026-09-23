"""The active tool's options in the status bar: its name, what it uses, and snapping.

What a tool uses shows only while it is active: the draw layer for the
drawing tools, the width for the Path tool and the angle step for the Rotate
tool. Snapping and the gizmos are always there.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QToolButton, QWidget

from mems_sketch.gui import icons


class ToolStatus(QWidget):
    """Tool name, tool options and snapping toggles, for the status bar."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 6, 0)
        row.setSpacing(4)
        self.tool_icon = QLabel()
        self.tool_name = QLabel()
        self.tool_name.setObjectName("heading")
        row.addWidget(self.tool_icon)
        row.addWidget(self.tool_name)

        self.layer_box = QComboBox()
        self.layer_box.setToolTip("The layer the drawing tools draw on")
        self.layer_box.setMinimumWidth(110)
        self.width_box = _number(
            0.001, 1e6, 3, " µm", "The width of paths drawn with the Path tool"
        )
        self.angle_box = _number(1, 90, 1, " °", "The Rotate tool snaps to multiples of this angle")
        self.tool_widgets: dict[str, tuple[QWidget, QWidget]] = {}
        for key, label, widget in (
            ("layer", "Layer", self.layer_box),
            ("width", "Width", self.width_box),
            ("angle", "Step", self.angle_box),
        ):
            caption = QLabel(f" {label}")
            caption.setObjectName("muted")
            row.addWidget(caption)
            row.addWidget(widget)
            self.tool_widgets[key] = (caption, widget)

        snapping = QLabel("   Snap")
        snapping.setObjectName("muted")
        row.addWidget(snapping)
        self._toggles = row
        # As tall as the tallest option, shown or not: the status bar (and so the
        # canvas above it) keeps its height when the tool changes.
        self.setFixedHeight(max(w.sizeHint().height() for w in (self.layer_box, self.width_box)))

    def add_toggle(self, action: QAction) -> None:
        """A small button for a checkable setting (snapping, gizmos)."""
        button = QToolButton()
        button.setDefaultAction(action)
        button.setAutoRaise(True)
        button.setIconSize(QSize(16, 16))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._toggles.addWidget(button)

    def show_for(self, tool) -> None:
        """Show the active tool and the options it uses."""
        self.tool_icon.setPixmap(icons.pixmap(tool.icon, 16))
        self.tool_name.setText(f"{tool.label} ")
        shown = {
            "layer": tool.draws and getattr(tool, "uses_layer", True),
            "width": tool.name == "path",
            "angle": tool.name == "rotate",
        }
        for key, widgets in self.tool_widgets.items():
            for widget in widgets:
                widget.setVisible(shown[key])


def _number(low: float, high: float, decimals: int, suffix: str, tip: str) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
    box.setRange(low, high)
    box.setDecimals(decimals)
    box.setSuffix(suffix)
    box.setToolTip(tip)
    return box
