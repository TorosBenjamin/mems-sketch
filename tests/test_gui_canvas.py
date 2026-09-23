import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QTransform, QWheelEvent
from PySide6.QtWidgets import QApplication

from mems_sketch.gui.canvas import LayoutCanvas

INT32 = 2**31 - 1


@pytest.fixture
def canvas(qtbot):
    c = LayoutCanvas()
    qtbot.addWidget(c)
    c.resize(1600, 1000)
    c.show()
    qtbot.waitExposed(c)
    return c


def wheel(canvas, pos: QPointF, up: bool = True) -> None:
    event = QWheelEvent(
        pos,
        canvas.viewport().mapToGlobal(pos),
        QPoint(0, 0),
        QPoint(0, 120 if up else -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(canvas.viewport(), event)


def move(canvas, pos: QPointF) -> None:
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        pos,
        canvas.viewport().mapToGlobal(pos),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(canvas.viewport(), event)


@pytest.mark.parametrize("up", [True, False])
def test_the_wheel_zooms_about_the_cursor(canvas, up):
    canvas.set_view_state(2.0, 300.0, -150.0)
    pos = QPointF(canvas.viewport().rect().center()) + QPointF(250, -180)
    move(canvas, pos)
    before = canvas.mapToScene(pos.toPoint())
    for _ in range(12):
        wheel(canvas, pos, up)
    after = canvas.mapToScene(pos.toPoint())
    tolerance = 2 / canvas.pixels_per_um()  # a pixel or two of rounding
    assert abs(after.x() - before.x()) <= tolerance
    assert abs(after.y() - before.y()) <= tolerance


def test_cursor_positions_fit_the_database_at_any_zoom(canvas):
    """klayout points are 32-bit integers of nm, so ±2.1 m is the edge of the world."""
    seen = []
    canvas.cursor_moved.connect(lambda x, y: seen.append((x, y)))
    canvas.setTransform(QTransform.fromScale(1.01e-4, -1.01e-4))  # zoomed out to the limit
    rect = canvas.viewport().rect()
    for corner in (rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight()):
        move(canvas, QPointF(corner))
    assert len(seen) == 4
    assert all(abs(round(v * 1000)) <= INT32 for p in seen for v in p)
