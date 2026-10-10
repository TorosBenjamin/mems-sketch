"""A quick check that an installed or bundled mems-sketch works: what a
release's packages are tested with (``mems-sketch --self-test``).

It builds a component that places a built-in one, checks the design rules in
a background process (as the editor does on big designs), writes and reads
back a GDS file, and opens and closes the main window. Each step prints a
line, and also goes to the file MEMS_SKETCH_SELFTEST_LOG names, if any (a
windowed app has no console to print to); any failure raises.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def say(line: str) -> None:
    print(line, flush=True)  # nothing happens without a console
    log = os.environ.get("MEMS_SKETCH_SELFTEST_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def run(window: bool = True) -> None:
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    from mems_sketch import RefShape, __version__, export
    from mems_sketch.core.imports import read_layout
    from mems_sketch.core.project import new_project
    from mems_sketch.process import rules

    say(f"mems-sketch {__version__}")
    from mems_sketch.engine import versions

    say(", ".join(f"{name} {number}" for name, number in versions().items()))

    project = new_project("self-test")
    project.components["top"].shapes = [
        RefShape(name="comb", component="comb_drive"),
        RefShape(name="spring", component="serpentine_spring", x=200),
    ]
    geometry = project.render("top")
    assert not geometry.is_empty(), "the test design drew nothing"
    say(f"built: {len(geometry.layer_names())} layers, {geometry.point_count()} points")

    with ProcessPoolExecutor(
        max_workers=1, mp_context=multiprocessing.get_context("spawn")
    ) as pool:
        violations = pool.submit(rules.check, project, geometry, "top").result(timeout=120)
    say(f"rules checked in another process: {len(violations)} finding(s)")

    with tempfile.TemporaryDirectory() as folder:
        path = export(project, Path(folder) / "self-test.gds")
        layout = read_layout(path.read_bytes())
        assert layout.cells, "the GDS file has no cells"
        say(f"GDS written and read back: {path.stat().st_size} bytes")

    if window:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from mems_sketch.gui.app import MainWindow

        app = QApplication.instance() or QApplication([])
        main = MainWindow()
        main.show()
        app.processEvents()
        main.close()
        say("main window opened and closed")
    say("self-test passed")
