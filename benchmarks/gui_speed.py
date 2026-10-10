"""How fast the editor is on big layouts: pan, zoom, hover, drag and edits.

Opens the real main window offscreen (1600 × 1000) on perforated plates from
the example library, with more and more holes, and times what a user does:

- pan, zoom, hover and the drag preview: one mouse event and the frame it
  draws (a frame under ~16 ms keeps up with a 60 Hz display);
- press, drop and a parameter edit: until the change is on screen; the
  rule checks of a big design finish later in the background ("checked");
- open: from the file to the first picture.

Run it from the repository root::

    uv run --extra dev python benchmarks/gui_speed.py
    uv run --extra dev python benchmarks/gui_speed.py --cases 40000 16x2500

The default cases take a few minutes; the 40,000-hole ones much longer
(every edit waits for its rule check, over a minute at that size).

Times are in milliseconds: medians, with the 90th percentile and the
slowest where an action repeats. They depend on the machine; compare runs on
the same one.
"""

from __future__ import annotations

import argparse
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QMessageBox

LIBRARY = Path(__file__).resolve().parent.parent / "examples" / "libraries" / "mems_std"
PITCH = 20  # µm between holes
CASES = {  # name: (plate size µm, plates per side)
    "100": (200, 1),
    "2500": (1000, 1),
    "10000": (2000, 1),
    "40000": (4000, 1),
    "16x2500": (1000, 4),
    "90000": (6000, 1),
}
LEFT, MIDDLE, NONE = Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton, Qt.MouseButton.NoButton


def write_project(folder: Path, size: float, copies: int) -> Path:
    """A project whose top component places ``copies`` × ``copies`` plates."""
    shutil.rmtree(folder, ignore_errors=True)  # no editor state from an earlier run
    (folder / "components" / "top").mkdir(parents=True)
    (folder / "project.yaml").write_text(
        f"format: mems-sketch/2\nname: bench\ntop: top\nlibraries: {{std: {LIBRARY}}}\n"
        "process: std.surface\ncomponents: {top: components/top}\n"
    )
    array = (
        f"    modifiers:\n    - {{kind: array, columns: {copies}, rows: {copies}, "
        f"dx: {size + 50}, dy: {size + 50}}}\n"
        if copies > 1
        else ""
    )
    (folder / "components" / "top" / "component.yaml").write_text(
        f"parameters:\n  pitch: {{default: {PITCH}, min: 2}}\nshapes:\n"
        f"  plate:\n    ref: std.perforated_plate\n    size: {size}\n    pitch: pitch\n{array}"
    )
    return folder / "project.yaml"


def ms(action) -> float:
    start = time.perf_counter()
    action()
    return (time.perf_counter() - start) * 1000


def mouse(canvas, kind, pos: QPoint, button=NONE, buttons=NONE) -> None:
    where = QPointF(pos)
    event = QMouseEvent(
        kind,
        where,
        canvas.viewport().mapToGlobal(where),
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(canvas.viewport(), event)


def wheel(canvas, pos: QPoint, up: bool) -> None:
    where = QPointF(pos)
    event = QWheelEvent(
        where,
        canvas.viewport().mapToGlobal(where),
        QPoint(0, 0),
        QPoint(0, 120 if up else -120),
        NONE,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(canvas.viewport(), event)


def wait_for_checks(app, view, limit_s: float = 120.0) -> None:
    deadline = time.perf_counter() + limit_s
    while view.checking and time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.002)


def run(app, path: Path) -> dict[str, float | list[float] | None]:
    from mems_sketch.gui.app import MainWindow

    window = MainWindow()
    window.resize(1600, 1000)
    window.show()
    app.processEvents()
    out: dict[str, float | list[float] | None] = {}
    out["open"] = ms(lambda: (window.open_project(str(path)), app.processEvents()))
    view, canvas = window.view, window.canvas
    canvas.configure(max_fps=0)  # every move handled: time the work, not the frame limit
    canvas.fit()
    frame = canvas.viewport().repaint
    out["first frame"] = ms(frame)
    out["checked (open)"] = ms(lambda: wait_for_checks(app, view))
    centre = canvas.viewport().rect().center()

    mouse(canvas, QEvent.Type.MouseButtonPress, centre, MIDDLE, MIDDLE)
    pans = []
    for i in range(1, 41):
        p = QPoint(centre.x() + (i % 20 - 10) * 20, centre.y() + (i % 10 - 5) * 10)
        pans.append(
            ms(lambda p=p: (mouse(canvas, QEvent.Type.MouseMove, p, buttons=MIDDLE), frame()))
        )
    mouse(canvas, QEvent.Type.MouseButtonRelease, centre, MIDDLE)
    out["pan"] = pans
    canvas.fit()

    out["zoom"] = [ms(lambda i=i: (wheel(canvas, centre, i < 8), frame())) for i in range(16)]
    for _ in range(10):  # draft quality ends after a pause
        app.processEvents()
        time.sleep(0.05)
    out["settled frame"] = ms(frame)
    canvas.fit()
    app.processEvents()

    hovers = []
    for i in range(30):
        p = QPoint(centre.x() - 300 + i * 20, centre.y() + 7)
        hovers.append(ms(lambda p=p: (mouse(canvas, QEvent.Type.MouseMove, p), frame())))
    out["hover"] = hovers

    window.set_tool("select")
    on_plate = None
    for dx in range(0, 400, 3):  # solid plate, not a hole
        q = QPoint(centre.x() + dx, centre.y() + 5)
        s = canvas.mapToScene(q)
        if window.hit(s.x(), s.y()) is not None:
            on_plate = q
            break
    if on_plate is not None:
        mouse(canvas, QEvent.Type.MouseMove, on_plate)
        frame()
        out["press"] = ms(
            lambda: (mouse(canvas, QEvent.Type.MouseButtonPress, on_plate, LEFT, LEFT), frame())
        )
        drags = []
        for i in range(1, 31):
            p = QPoint(on_plate.x() + i * 4, on_plate.y() + i * 2)
            drags.append(
                ms(lambda p=p: (mouse(canvas, QEvent.Type.MouseMove, p, buttons=LEFT), frame()))
            )
        out["drag"] = drags
        end = QPoint(on_plate.x() + 120, on_plate.y() + 60)
        out["drop"] = ms(
            lambda: (mouse(canvas, QEvent.Type.MouseButtonRelease, end, LEFT), frame())
        )
        out["checked (drop)"] = ms(lambda: wait_for_checks(app, window.view))

    edits, checks = [], []
    for k in range(1, 6):  # new values each time: nothing comes from a cache
        value = PITCH + k * 0.01

        def edit(value=value):
            window.run(lambda: window.document.parameters.update("pitch", default=value))
            window.canvas.viewport().repaint()

        edits.append(ms(edit))
        checks.append(ms(lambda: wait_for_checks(app, window.view)))
    out["parameter edit"] = edits
    out["checked (edit)"] = checks
    out["undo"] = ms(lambda: (window.run(window.document.undo), window.canvas.viewport().repaint()))
    window.close()
    window.deleteLater()
    app.processEvents()
    return out


def summary(value: float | list[float] | None) -> str:
    if value is None or value == []:
        return "-"
    if isinstance(value, list):
        xs = sorted(value)
        p90 = xs[max(0, round(len(xs) * 0.9) - 1)]
        return f"{statistics.median(xs):8.1f}  (p90 {p90:.1f}, max {xs[-1]:.1f})"
    return f"{value:8.1f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cases", nargs="+", choices=list(CASES), default=["100", "2500", "10000"])
    parser.add_argument(
        "--folder", type=Path, help="where to write the projects (default: a temporary folder)"
    )
    args = parser.parse_args(argv)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Discard)
    app = QApplication.instance() or QApplication([])
    root = args.folder or Path(tempfile.mkdtemp(prefix="mems-sketch-bench-"))
    for name in args.cases:
        size, copies = CASES[name]
        holes = (size // PITCH) ** 2 * copies**2
        print(
            f"\n{name}: {holes:,} holes ({copies}x{copies} plate{'s' if copies > 1 else ''} of {size} µm)"
        )
        result = run(app, write_project(root / name, size, copies))
        for key, value in result.items():
            print(f"  {key:16s} {summary(value)}")
        sys.stdout.flush()
    if args.folder is None:
        shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
