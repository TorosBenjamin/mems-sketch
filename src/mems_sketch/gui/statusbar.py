"""The status bar, in three parts.

On the left the active tool (its icon and name, and what it uses: the draw
layer for the drawing tools, the width for the Path tool, the angle step for
the Rotate tool) and, dimmed, what to do now. In the middle the problems
(errors, rule violations; a click shows them). On the right snapping and the
gizmos, the grid step, the zoom, the point being snapped to and the cursor
position, each in a field of its own width so that nothing jumps as they change.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QStatusBar,
    QToolButton,
    QWidget,
)

from mems_sketch.gui import icons
from mems_sketch.gui.widgets import ElidedLabel

STATUS_HEIGHT = 22  # px: the status bar's items and the tool options in it


class ToolStatus(QWidget):
    """Tool name, tool options and snapping toggles, for the status bar."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 6, 0)
        row.setSpacing(4)
        self.tool_icon = QLabel()
        self.tool_name = QLabel()
        self.tool_name.setObjectName("status-tool")
        row.addWidget(self.tool_icon)
        row.addWidget(self.tool_name)

        self.layer_box = QComboBox()
        self.layer_box.setToolTip("The layer the drawing tools draw on")
        self.layer_box.setMinimumWidth(110)
        self.width_box = _number(
            0.001, 1e6, 3, " µm", "The width of paths drawn with the Path tool"
        )
        self.angle_box = _number(1, 90, 1, " °", "The Rotate tool snaps to multiples of this angle")
        # The Measure tool's modes, side by side: Distance | Angle.
        self.measure_modes = QWidget()
        modes = QHBoxLayout(self.measure_modes)
        modes.setContentsMargins(0, 0, 0, 0)
        modes.setSpacing(1)
        self.mode_buttons: dict[str, QToolButton] = {}
        group = QButtonGroup(self.measure_modes)
        for mode, text, icon, tip in (
            ("distance", "Distance", "measure", "Measure distances: two clicks (D)"),
            ("angle", "Angle", "angle", "Measure angles: the vertex, then each arm (N)"),
        ):
            button = QToolButton()
            button.setText(text)
            icons.bind(button, icon)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setToolTip(tip)
            button.setFixedHeight(STATUS_HEIGHT)
            group.addButton(button)
            modes.addWidget(button)
            self.mode_buttons[mode] = button
        self.tool_widgets: dict[str, tuple[QWidget, QWidget]] = {}
        for key, label, widget in (
            ("layer", "Layer", self.layer_box),
            ("width", "Width", self.width_box),
            ("angle", "Step", self.angle_box),
            ("mode", "", self.measure_modes),
        ):
            caption = QLabel(f" {label}")
            caption.setObjectName("muted")
            row.addWidget(caption)
            row.addWidget(widget)
            self.tool_widgets[key] = (caption, widget)

        # Snapping and the gizmos: shown on the right of the status bar.
        self.snapping = QWidget()
        toggles = QHBoxLayout(self.snapping)
        toggles.setContentsMargins(0, 0, 0, 0)
        toggles.setSpacing(1)
        caption = QLabel("Snap")
        caption.setObjectName("muted")
        toggles.addWidget(caption)
        self._toggles = toggles
        # Compact inputs, as tall as the status bar's other items; one fixed height
        # keeps the status bar (and so the canvas above it) still when the tool changes.
        for box in (self.layer_box, self.width_box, self.angle_box):
            box.setFixedHeight(STATUS_HEIGHT)
        self.setFixedHeight(STATUS_HEIGHT)

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
        self.tool_name.setText(tool.label)
        shown = {
            "layer": tool.draws and getattr(tool, "uses_layer", True),
            "width": tool.name == "path",
            "angle": tool.name == "rotate",
            "mode": tool.name == "measure",
        }
        mode = getattr(tool, "mode", None)
        if mode in self.mode_buttons:
            self.mode_buttons[mode].setChecked(True)
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


GRID_SAMPLE = "grid 0.05 µm"  # what each field is sized for: it keeps that width
ZOOM_SAMPLE = "100 px/µm"
POSITION_SAMPLE = "x -1000.000  y -1000.000"
TARGET_MAX_WIDTH = 220  # px at most for the point the cursor would snap to
HINT_LEAST_PX = 90  # a hint with less room than this is left out, not cut to "Cli…"


class _Hint(ElidedLabel):
    """What to do now: cut with "…" when it does not fit, left out when only a few
    letters would show."""

    def _elide(self) -> None:
        if self.width() < HINT_LEAST_PX:
            QLabel.setText(self, "")
            self.setToolTip(self.text())
            return
        super()._elide()


class StatusBar(QStatusBar):
    """See the module docstring. :meth:`showMessage` shows what to do now, dimmed,
    after the tool (not over the bar's fields, as a plain status bar does)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizeGripEnabled(False)
        self.hint = _Hint()
        self.hint.setObjectName("status-hint")
        self._message = ""
        self._clear = QTimer(self, singleShot=True)
        self._clear.timeout.connect(self.clearMessage)
        self.row = QWidget()
        self.row.setObjectName("status-row")
        self._layout = QHBoxLayout(self.row)
        self._layout.setContentsMargins(6, 0, 8, 0)
        self._layout.setSpacing(0)
        self.addPermanentWidget(self.row, 1)

    def arrange(
        self, tool: ToolStatus, target: QWidget, problems: QWidget, fields: list[QWidget]
    ) -> None:
        """Lay the bar out: the tool and the hint, the point the cursor would snap to
        and the problems (these may change width: they take the room the hint
        leaves), then the fixed fields."""
        layout = self._layout
        layout.addWidget(tool)
        layout.addSpacing(10)
        layout.addWidget(self.hint, 1)
        layout.addWidget(target)
        layout.addSpacing(6)
        layout.addWidget(problems)
        for field in fields:
            layout.addSpacing(4)
            layout.addWidget(_separator())
            layout.addSpacing(4)
            layout.addWidget(field)

    # -- the hint ------------------------------------------------------------------

    def showMessage(self, text: str, timeout: int = 0) -> None:
        self._message = text
        self.hint.setText(text)
        if timeout > 0:
            self._clear.start(timeout)
        else:
            self._clear.stop()

    def currentMessage(self) -> str:
        return self._message

    def clearMessage(self) -> None:
        self._message = ""
        self.hint.setText("")


def fixed_label(sample: str, tip: str, align=Qt.AlignmentFlag.AlignHCenter) -> QLabel:
    """A status field as wide as ``sample``, whatever it shows."""
    label = QLabel()
    label.setToolTip(tip)
    label.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
    label.setFixedWidth(label.fontMetrics().horizontalAdvance(sample) + 20)  # + padding
    return label


def _separator() -> QFrame:
    line = QFrame()
    line.setObjectName("status-separator")
    line.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    line.setFixedSize(1, 14)
    return line
