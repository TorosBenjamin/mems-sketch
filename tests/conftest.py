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
