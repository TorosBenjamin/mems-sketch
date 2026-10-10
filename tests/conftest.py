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
    from mems_sketch.core.region import IntPolygon
    from mems_sketch.layout import Layout, Placement, gds

    layout = Layout(dbu=dbu)
    top = layout.cell(cell)
    child = layout.cell("PART")
    for index, (layer, datatype, x0, y0, x1, y1) in enumerate(boxes):
        target = child if index == len(boxes) - 1 else top
        l, b, r, t = (round(v / dbu) for v in (x0, y0, x1, y1))
        target.add((layer, datatype), IntPolygon([(l, b), (r, b), (r, t), (l, t)], []))
    top.placements.append(Placement("PART"))
    path.write_bytes(gds.write(layout))
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
    _run_git(folder.parent, "config", "user.name", "Test")  # the app commits too
    _run_git(folder.parent, "config", "user.email", "test@example.com")
    return folder


@pytest.fixture
def commit():
    """Save a session's project and commit it: ``commit(session, message)``."""
    return _commit
