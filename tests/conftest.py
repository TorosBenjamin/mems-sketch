import os

# GUI tests never open windows, even when the shell sets a platform (e.g. wayland).
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path):
    """Keep the GUI's remembered state (last folder, open tabs) out of the user's settings."""
    try:
        from PySide6.QtCore import QSettings
    except ImportError:
        yield
        return
    for fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
        QSettings.setPath(fmt, QSettings.Scope.UserScope, str(tmp_path / "settings"))
    yield


def _write_gds(path, boxes, dbu=0.001, cell="FRAME"):
    """A GDS file with ``boxes``: (layer, datatype, x0, y0, x1, y1) in µm, the
    last box inside a sub-cell (so the import has to flatten)."""
    import klayout.db as kdb

    layout = kdb.Layout()
    layout.dbu = dbu
    top = layout.create_cell(cell)
    child = layout.create_cell("PART")
    for index, (layer, datatype, *box) in enumerate(boxes):
        target = child if index == len(boxes) - 1 else top
        target.shapes(layout.layer(layer, datatype)).insert(kdb.DBox(*box))
    top.insert(kdb.DCellInstArray(child.cell_index(), kdb.DTrans()))
    layout.write(str(path))
    return path


@pytest.fixture
def write_gds():
    """Write a small GDS file: see ``_write_gds``."""
    return _write_gds


def _run_git(folder, *args):
    import subprocess

    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=folder,
        check=True,
        capture_output=True,
    )


def _commit(session, message):
    """Save the session's project and commit it (the whole repository)."""
    session.save()
    _run_git(session.path, "add", "-A", ".")
    _run_git(session.path, "commit", "-q", "-m", message)


@pytest.fixture
def git_repo(tmp_path):
    """An empty git repository; returns a folder in it for a project."""
    folder = tmp_path / "repo" / "demo"
    folder.mkdir(parents=True)
    _run_git(folder.parent, "init", "-q")
    return folder


@pytest.fixture
def commit():
    """Save a session's project and commit it: ``commit(session, message)``."""
    return _commit
