"""Regenerate the pictures in docs/images from the example project.

    python docs/screenshots.py            # all of them
    python docs/screenshots.py history    # the ones whose name contains "history"

Runs offscreen (no window opens) on a copy of examples/resonator, with
fresh settings, so the pictures do not depend on the machine. Run it after a
visible change to the GUI and commit the pictures that changed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "docs" / "images"
EXAMPLES = ROOT / "examples"
SIZE = (1400, 860)

sys.path.insert(0, str(ROOT / "src"))

from mems_sketch.core.shapes import RectShape
from mems_sketch.core.shapes.modifiers import Corner, CornersModifier
from mems_sketch.gui.app import MainWindow

SHOTS = {}


def shot(function):
    SHOTS[function.__name__] = function
    return function


def settle(app: QApplication, rounds: int = 8) -> None:
    for _ in range(rounds):
        app.processEvents()


def save(widget: QWidget, name: str) -> None:
    settle(QApplication.instance())
    widget.grab().save(str(IMAGES / f"{name}.png"))
    print(f"docs/images/{name}.png")


class Workspace:
    """A copy of the examples (the library sits beside the project, as in the repository)."""

    def __init__(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mems-sketch-shots-"))
        shutil.copytree(EXAMPLES, self.tmp / "examples")
        self.project = self.tmp / "examples" / "resonator"
        settings = self.tmp / "settings"
        for fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
            QSettings.setPath(fmt, QSettings.Scope.UserScope, str(settings))

    def window(self, *panels: str, component: str | None = None) -> MainWindow:
        window = MainWindow()
        window.resize(*SIZE)
        window.open_project(str(self.project / "project.yaml"))
        for name in window.tool_windows.names():
            window.tool_windows.close(name)
        for name in panels:
            window.tool_windows.open(name)
        if component is not None:
            window.open_component(component)
        window.show()
        settle(QApplication.instance())
        window.canvas.fit()
        return window

    def close(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


# -- the pictures ----------------------------------------------------------------------


@shot
def window(ws: Workspace) -> None:
    w = ws.window("components", "shapes", "properties", "messages")
    w.tree.select_paths([((0, 1),)])
    save(w, "window")


@shot
def tabs_split(ws: Workspace) -> None:
    w = ws.window("components")
    w.open_aside("suspension")
    for view in w.area.views():
        view.canvas.fit()
    save(w, "split-view")


@shot
def parameters(ws: Workspace) -> None:
    w = ws.window("components", "parameters")
    save(w, "parameters")


@shot
def points(ws: Workspace) -> None:
    from mems_sketch.gui.points_panel import KEY_ROLE

    w = ws.window("points")
    tree = w.points.tree
    for index in range(tree.topLevelItemCount()):
        group = tree.topLevelItem(index)
        group.setExpanded(group.text(0) == "suspension_left")
        for child in range(group.childCount()):
            if group.child(child).data(0, KEY_ROLE) == ("suspension_left", "top_left"):
                tree.setCurrentItem(group.child(child))
    save(w, "points")


@shot
def import_dialog(ws: Workspace) -> None:
    from mems_sketch.export.base import export
    from mems_sketch.gui.import_dialog import ImportDialog

    w = ws.window("components")
    frame = ws.tmp / "pad frame.gds"
    export(w.document.project, frame)
    dialog = ImportDialog(w.document, frame, w)
    dialog.show()
    save(dialog, "import-dialog")


@shot
def align(ws: Workspace) -> None:
    w = ws.window("shapes")
    w.tree.select_paths([((0, 3),)])
    w.set_tool("align")
    save(w.canvas, "align-tool")


@shot
def corners(ws: Workspace) -> None:
    w = ws.window("shapes", "properties")
    doc = w.document
    doc.set_active("top")
    path = doc.nodes.add(
        RectShape(
            name="pad",
            layer="device",
            x0=-260,
            y0=-40,
            x1=-200,
            y1=20,
            modifiers=[
                CornersModifier(
                    corners=[
                        Corner(at="self.top_right", radius=12),
                        Corner(at="self.bottom_left", radius=8, style="chamfer"),
                    ]
                )
            ],
        )
    )
    w.tree.select_paths([path])
    w.set_tool("corners")
    w.canvas.fit()
    save(w, "corners")


@shot
def components_panel(ws: Workspace) -> None:
    w = ws.window("components")
    save(w.components, "components")


@shot
def process(ws: Workspace) -> None:
    w = ws.window("components")
    w.open_process()
    save(w, "process")


@shot
def history(ws: Workspace) -> None:
    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=Designer", "-c", "user.email=d@example.com", *args],
            cwd=ws.project,
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    git("add", "-A", ".")
    git("commit", "-q", "-m", "Resonator, first version")
    w = ws.window("shapes", "history")
    doc = w.document
    comb = doc.node(((0, 1),))
    data = comb.model_dump()
    data["params"] = {**data["params"], "fingers": 10}
    doc.nodes.replace(((0, 1),), type(comb).model_validate(data))
    save(w, "history")


@shot
def settings_dialog(ws: Workspace) -> None:
    from mems_sketch.gui.settings import PreferencesDialog

    w = ws.window()
    dialog = PreferencesDialog(w.settings, [], w)
    dialog.show()
    save(dialog, "settings")


def main(argv: list[str]) -> int:
    IMAGES.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    # The pictures edit their copy of the example; closing them discards that.
    QMessageBox.question = lambda *a, **k: QMessageBox.StandardButton.Discard
    chosen = [n for n in SHOTS if not argv or any(a in n for a in argv)]
    for name in chosen:
        ws = Workspace()
        try:
            SHOTS[name](ws)
        finally:
            for widget in app.topLevelWidgets():
                widget.close()
                widget.deleteLater()
            settle(app)
            ws.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
