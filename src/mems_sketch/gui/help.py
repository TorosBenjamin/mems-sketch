"""A small "?" button that explains something when hovered, instead of a paragraph
of text in the way."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtWidgets import QToolButton, QToolTip, QWidget

from mems_sketch.gui import icons

WIDTH_PX = 320  # explanations wrap at this width


def help_html(text: str) -> str:
    """``text`` as a tooltip that wraps: paragraphs are separated by blank lines,
    and ``*x*`` is written in bold."""
    paragraphs = []
    for paragraph in text.strip().split("\n\n"):
        parts = escape(" ".join(paragraph.split())).split("*")
        paragraphs.append(
            "".join(f"<b>{part}</b>" if i % 2 else part for i, part in enumerate(parts))
        )
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return f"<table width={WIDTH_PX}><tr><td>{body}</td></tr></table>"


class HelpButton(QToolButton):
    """Shows its explanation at once when hovered (or clicked), under itself."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.text_ = text
        icons.bind(self, "help")
        self.setIconSize(QSize(14, 14))
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setToolTip(help_html(text))  # also what a screen reader reads
        self.setAccessibleName("Help")
        self.clicked.connect(self.show_help)

    def show_help(self) -> None:
        QToolTip.showText(self.mapToGlobal(QPoint(0, self.height())), self.toolTip(), self)

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.show_help()
