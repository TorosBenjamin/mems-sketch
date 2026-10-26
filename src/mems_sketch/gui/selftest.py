"""A quick check that an installed or bundled mems-sketch works: what a
release's packages are tested with (``mems-sketch --self-test``).

It builds a component that places components of the bundled mems library
(so the bundled libraries are in the package), checks the design rules in
a background process (as the editor does on big designs), writes and reads
back a GDS file, meshes it (when gmsh is there), and opens and closes the main window. Each step prints a
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

    project = new_project("selftest")  # a cell name every tool takes
    project.components["top"].shapes = [
        RefShape(name="comb", component="mems.comb_drive"),
        RefShape(name="spring", component="mems.serpentine_spring", x=200),
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
        say(f"mesh: {mesh_check(geometry)}")

    say(f"file types: {file_types()}")

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


def mesh_check(geometry) -> str:
    """A coarse mesh of the test design, when gmsh is here (an app bundles it;
    with pip it is an extra, so its absence is said, not a failure)."""
    from mems_sketch import meshing
    from mems_sketch.core.mesh_settings import MeshSettings

    if not meshing.available():
        return "gmsh not installed (meshing is off)"
    mesh = meshing.generate(geometry, MeshSettings(size=20))
    assert mesh.element_count > 0, "the mesh has no elements"
    return mesh.summary()


def file_types() -> str:
    """Whether .msketch files are registered here (#140), as far as can be seen:
    the macOS bundle declares the type; the Windows installer writes it to the
    registry (the portable zip does not); on Linux the MIME type is installed.
    Only what is found is said: a missing registration is not a failure."""
    import sys
    import xml.etree.ElementTree as ET

    from mems_sketch.gui.associations import MIME_TYPE, MIME_XML

    ET.fromstring(MIME_XML)  # what Linux gets must be valid
    if sys.platform == "darwin" and getattr(sys, "frozen", False):
        import plistlib

        info = Path(sys.executable).resolve().parents[1] / "Info.plist"
        if info.is_file():
            declared = plistlib.loads(info.read_bytes()).get("UTExportedTypeDeclarations", [])
            if not any("msketch" in str(t) for t in declared):
                raise AssertionError("the app bundle does not declare .msketch files")
            return ".msketch declared by the app bundle"
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.msketch") as key:
                return f".msketch registered as {winreg.QueryValue(key, None)}"
        except OSError:
            return ".msketch not registered (the portable zip registers nothing)"
    share = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    if (share / "mime" / "packages" / "mems-sketch.xml").is_file():
        return f"{MIME_TYPE} installed for this user"
    return f"{MIME_TYPE} not installed (Help → Install file associations)"
