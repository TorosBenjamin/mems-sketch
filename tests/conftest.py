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
