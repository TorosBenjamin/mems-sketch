"""The Welcome window: shown when the program starts without a project, and when
one is closed (File › Close project).

On the left the program's name and version. On the right a search field, **New
project** (the wizard) and **Open…** (the only place that opens the system's
file picker), then the projects opened lately: pinned ones first, then the most
recent. A row shows the project's icon (a library has its own), name and folder,
when it was last opened and the git branch it is on. Pointing at a row shows a
pin; a right-click pins, shows the folder, locates a project whose folder has
gone, or takes it off the list. Enter or a double-click opens it.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.gui import icons, recent_projects, theme
from mems_sketch.gui.recent_projects import Recent

RECENT_ROLE = Qt.ItemDataRole.UserRole + 50  # a row: its Recent
ROW_HEIGHT = 52
PIN_SIZE = 16
WIDTH, HEIGHT = 820, 540
SIDE_WIDTH = 210
OPEN_FILTER = "Projects (project.yaml *.mems *.msketch);;All files (*)"


def short_path(path: str) -> str:
    """A folder for showing: the home folder as ``~``."""
    home = str(Path.home())
    return "~" + path[len(home) :] if path.startswith(home) else path


class ProjectDelegate(QStyledItemDelegate):
    """A recent project's row: icon, name and folder on the left; when it was
    opened and its branch on the right; a pin when pinned or pointed at."""

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), ROW_HEIGHT)

    def pin_rect(self, rect: QRect) -> QRect:
        return QRect(
            rect.right() - 12 - PIN_SIZE, rect.center().y() - PIN_SIZE // 2, PIN_SIZE, PIN_SIZE
        )

    def paint(self, painter: QPainter, option, index) -> None:
        recent: Recent | None = index.data(RECENT_ROLE)
        if recent is None:
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if option.state & QStyle.StateFlag.State_Selected or hovered:
            path = QPainterPath()
            path.addRoundedRect(QRectF(rect).adjusted(2, 2, -2, -2), 8, 8)
            selected = option.state & QStyle.StateFlag.State_Selected
            painter.fillPath(path, theme.color("selected" if selected else "hover"))
        text, muted = theme.color("text"), theme.color("muted")
        missing = recent.missing
        glyph = "component_library" if recent.library else "component"
        painter.setOpacity(0.45 if missing else 1.0)
        painter.drawPixmap(rect.left() + 14, rect.center().y() - 12, icons.pixmap(glyph, 24))
        painter.setOpacity(1.0)
        left = rect.left() + 52
        right_text = rect.right() - 12 - PIN_SIZE - 12
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        right_flags = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # The right column: when, and the branch.
        small = QFont(option.font)
        small.setPointSizeF(option.font.pointSizeF() * 0.92)
        painter.setFont(small)
        painter.setPen(theme.color("error") if missing else muted)
        top = QRect(left, rect.top() + 8, right_text - left, 18)
        bottom = QRect(left, rect.top() + 27, right_text - left, 18)
        meta_top = "missing" if missing else recent_projects.when(recent.opened)
        painter.drawText(top, right_flags, meta_top)
        width_top = painter.fontMetrics().horizontalAdvance(meta_top)
        branch = None if missing else recent.branch
        width_bottom = 0
        if branch:
            painter.setPen(muted)
            painter.drawText(bottom, right_flags, branch)
            width_bottom = painter.fontMetrics().horizontalAdvance(branch) + 18
            glyph_x = bottom.right() - width_bottom + 1
            painter.drawPixmap(glyph_x, bottom.center().y() - 6, icons.pixmap("branch", 12))
        # The left column: name, then the folder.
        name_font = QFont(option.font)
        name_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(name_font)
        painter.setPen(muted if missing else text)
        room = top.width() - width_top - 16
        painter.drawText(
            QRect(left, top.top(), room, top.height()),
            flags,
            painter.fontMetrics().elidedText(recent.name, Qt.TextElideMode.ElideRight, room),
        )
        painter.setFont(small)
        painter.setPen(muted)
        room = bottom.width() - width_bottom - 16
        folder = short_path(recent.path)
        painter.drawText(
            QRect(left, bottom.top(), room, bottom.height()),
            flags,
            painter.fontMetrics().elidedText(folder, Qt.TextElideMode.ElideMiddle, room),
        )
        if recent.pinned or hovered:
            painter.setOpacity(1.0 if recent.pinned else 0.55)
            color = theme.color("accent").name() if recent.pinned else None
            painter.drawPixmap(self.pin_rect(rect), icons.pixmap("pin", PIN_SIZE, color))
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        """A click on the pin pins or unpins the project (and opens nothing)."""
        recent = index.data(RECENT_ROLE)
        if (
            recent is not None
            and event.type() == QEvent.Type.MouseButtonRelease
            and self.pin_rect(option.rect).contains(event.position().toPoint())
        ):
            recent_projects.pin(recent.path, not recent.pinned)
            self.parent().pinned.emit()
            return True
        return super().editorEvent(event, model, option, index)


class ProjectList(QListWidget):
    pinned = Signal()  # a pin was clicked: the list is read again


class WelcomeWindow(QWidget):
    """See the module docstring. Emits ``open_project`` with a path (a project
    folder, its manifest or a project document) or ``new_project``."""

    open_project = Signal(str)
    new_project = Signal()

    def __init__(self, version: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("welcome")
        self.setWindowTitle("Welcome to MEMS Sketch")
        self.setWindowIcon(icons.icon("component"))
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.resize(WIDTH, HEIGHT)

        side = QFrame()
        side.setObjectName("welcome-side")
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        side.setFixedWidth(SIDE_WIDTH)
        logo = QLabel()
        logo.setPixmap(icons.pixmap("component", 40))
        title = QLabel("MEMS Sketch")
        title.setObjectName("welcome-title")
        subtitle = QLabel(version)
        subtitle.setObjectName("muted")
        column = QVBoxLayout(side)
        column.setContentsMargins(20, 24, 20, 20)
        column.setSpacing(4)
        column.addWidget(logo)
        column.addSpacing(8)
        column.addWidget(title)
        column.addWidget(subtitle)
        column.addStretch(1)

        self.search = QLineEdit()
        self.search.setObjectName("welcome-search")
        self.search.setPlaceholderText("Search projects")
        self.search.addAction(icons.icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.fill)
        self.search.installEventFilter(self)
        self.new_button = QPushButton("New project")
        self.new_button.setDefault(True)
        self.new_button.setAutoDefault(False)
        self.new_button.clicked.connect(self.new_project)
        self.open_button = QPushButton("Open…")
        self.open_button.setAutoDefault(False)
        self.open_button.setToolTip("Open a project folder, its project.yaml or a project file")
        self.open_button.clicked.connect(self.choose)
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(self.search, 1)
        header.addWidget(self.new_button)
        header.addWidget(self.open_button)

        self.list = ProjectList()
        self.list.setObjectName("welcome-list")
        self.list.setItemDelegate(ProjectDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.itemActivated.connect(self._activated)
        self.list.pinned.connect(lambda: self.fill(self.search.text()))
        self.empty = QLabel()
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)

        main = QWidget()
        main.setObjectName("welcome-main")
        body = QVBoxLayout(main)
        body.setContentsMargins(20, 20, 20, 16)
        body.setSpacing(12)
        body.addLayout(header)
        body.addWidget(self.list, 1)
        body.addWidget(self.empty, 1)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(side)
        layout.addWidget(main, 1)
        self.fill("")
        self.search.setFocus()

    # -- the list -----------------------------------------------------------------

    def fill(self, text: str = "") -> None:
        words = text.lower().split()
        self.list.clear()
        everything = recent_projects.load()
        for recent in everything:
            haystack = f"{recent.name} {recent.path}".lower()
            if all(word in haystack for word in words):
                item = QListWidgetItem(recent.name)
                item.setData(RECENT_ROLE, recent)
                item.setToolTip(recent.path)
                self.list.addItem(item)
        shown = self.list.count()
        if shown:
            self.list.setCurrentRow(0)
        elif everything:
            self.empty.setText(f"No project matches “{text.strip()}”.")
        else:
            self.empty.setText(
                "No recent projects yet.\nStart a new project, or open one you have."
            )
        self.list.setVisible(bool(shown))
        self.empty.setVisible(not shown)

    def rows(self) -> list[Recent]:
        return [self.list.item(i).data(RECENT_ROLE) for i in range(self.list.count())]

    def _activated(self, item: QListWidgetItem) -> None:
        recent: Recent = item.data(RECENT_ROLE)
        if recent.missing:
            self.locate(recent)
            return
        self.open_project.emit(recent.path)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.search and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self.list.count():
                row = self.list.currentRow() + (1 if key == Qt.Key.Key_Down else -1)
                self.list.setCurrentRow(max(0, min(row, self.list.count() - 1)))
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.list.currentItem():
                self._activated(self.list.currentItem())
                return True
        return super().eventFilter(watched, event)

    # -- what a project's row offers ------------------------------------------------

    def _menu(self, position: QPoint) -> None:
        item = self.list.itemAt(position)
        if item is None:
            return
        menu = self.menu_for(item.data(RECENT_ROLE))
        menu.exec(self.list.viewport().mapToGlobal(position))

    def menu_for(self, recent: Recent) -> QMenu:
        menu = QMenu(self)
        if recent.missing:
            menu.addAction("Locate…", lambda: self.locate(recent))
        else:
            menu.addAction(icons.icon("open"), "Open", lambda: self.open_project.emit(recent.path))
            menu.addAction(
                "Show in folder",
                lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(recent.path)),
            )
        menu.addSeparator()
        menu.addAction(
            "Unpin" if recent.pinned else "Pin to the top",
            lambda: (
                recent_projects.pin(recent.path, not recent.pinned),
                self.fill(self.search.text()),
            ),
        )
        menu.addAction(
            "Remove from the list",
            lambda: (recent_projects.forget(recent.path), self.fill(self.search.text())),
        )
        return menu

    def locate(self, recent: Recent) -> None:
        """A project whose folder has gone: find it again (the list follows)."""
        folder = self.pick_folder(f"Where is {recent.name} now?")
        if not folder:
            return
        if not (Path(folder) / "project.yaml").exists():
            self.empty.setText(f"{folder} holds no project.")
            return
        recent_projects.locate(recent.path, folder)
        self.fill(self.search.text())

    # -- the file picker (tests answer instead) ---------------------------------------

    def choose(self) -> None:
        """Open…: a project folder's manifest or a project file, chosen in the picker."""
        path = self.pick_file()
        if path:
            self.open_project.emit(path)

    def pick_file(self) -> str:
        path, _ = QFileDialog.getOpenFileName(self, "Open project", str(Path.home()), OPEN_FILTER)
        return path

    def pick_folder(self, title: str) -> str:
        return QFileDialog.getExistingDirectory(self, title, str(Path.home()))
