"""The Process tab: the process's constants and layer definitions.

They are project data like the components, but set once per process and
rarely changed, so they open in an editor tab (from the project tree or
☰ › View › Process) rather than taking a panel. Edits go through the edit
session like any other, with undo.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QSplitter, QVBoxLayout, QWidget

from mems_sketch.editing import EditSession
from mems_sketch.gui.panels import ConstantsPanel, LayerDefinitionsPanel


class ProcessView(QWidget):
    """An editor tab with the constants and the layer definitions."""

    error = Signal(str)
    component = None  # not a component: the editor area's lookups by name skip it

    def __init__(self, document: EditSession) -> None:
        super().__init__()
        self.document = document
        self.constants = ConstantsPanel(document)
        self.layers = LayerDefinitionsPanel(document)
        for panel in (self.constants, self.layers):
            panel.error.connect(self.error)
        heading = QLabel("Process: constants for expressions (process.<name>) and layers")
        heading.setObjectName("heading")
        heading.setContentsMargins(10, 8, 10, 6)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.constants)
        split.addWidget(self.layers)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(heading)
        layout.addWidget(split, 1)
        self.refresh()

    def refresh(self) -> None:
        self.constants.refresh()
        self.layers.refresh()

    # The tab's label, icon and tooltip, as for a component tab.
    def title(self) -> str:
        return "process"

    def icon_name(self) -> str:
        return "layers"

    def tooltip(self) -> str:
        return "The process: constants and layer definitions"
