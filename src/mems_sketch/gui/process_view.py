"""The Process tab: the process the project uses, its constants, layer
definitions and design rules.

They are project data like the components, but set once per process and
rarely changed, so they open in an editor tab (from the project tree or
☰ › View › Process) rather than taking a panel. The tab is a page of cards at
a readable width, with the process picker in its header. Edits go through the
edit session like any other, with undo.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from mems_sketch.editing import EditSession
from mems_sketch.gui import theme, widgets
from mems_sketch.gui.help import HelpButton
from mems_sketch.gui.panels import ConstantsPanel, LayerDefinitionsPanel
from mems_sketch.gui.rules_panel import RulesPanel


class ProcessView(QWidget):
    """An editor tab with the process picker, the constants, the layer definitions
    and the rules."""

    error = Signal(str)
    component = None  # not a component: the editor area's lookups by name skip it

    def __init__(self, document: EditSession) -> None:
        super().__init__()
        self.document = document
        self.constants = ConstantsPanel(document)
        self.layers = LayerDefinitionsPanel(document)
        self.rules = RulesPanel(document)
        for panel in (self.constants, self.layers, self.rules):
            panel.error.connect(self.error)
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.metric("space_m"))
        heading = QLabel("Process")
        heading.setObjectName("heading")
        row.addWidget(heading)
        row.addWidget(self.rules.process)  # which process: it decides everything below
        row.addWidget(
            HelpButton(
                "What the whole project is made with: its constants, which every "
                "expression can use, its layers, and the rules they are checked with. "
                "Changes here affect every component."
            )
        )
        row.addStretch(1)
        self.page = widgets.page(self.constants, self.layers, self.rules, header=header)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.page)
        self.refresh()

    def refresh(self) -> None:
        self.constants.refresh()
        self.layers.refresh()
        self.rules.refresh()

    # The tab's label, icon and tooltip, as for a component tab.
    def title(self) -> str:
        return "process"

    def icon_name(self) -> str:
        return "process"

    def tooltip(self) -> str:
        return "The process: constants, layer definitions and rules"
