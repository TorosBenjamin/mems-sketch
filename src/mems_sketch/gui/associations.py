"""Opening ``.msketch`` files from the desktop (#140).

- **Where a file goes:** a project already open in a window comes to the front
  (with the component's tab when a component's file was opened); another one
  opens in a new window (:func:`route`).
- **One running app:** started with a file while it is already running, the
  app hands the file to the running one and quits (:func:`forward`), so a
  double-click in Explorer or a file manager lands in the open editor. The
  running app listens for that (:func:`listen`).
- **macOS** hands a double-clicked file over as an open event, also to a
  running app: :class:`OpenEvents` takes it.
- **Linux:** the AppImage cannot register itself; :func:`install_linux` writes
  the MIME type, the desktop entry and the icon to ``~/.local/share`` (Help →
  Install file associations). The Windows installer and the macOS bundle
  register the extension themselves (packaging/).
"""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

MIME_TYPE = "application/x-mems-sketch"
# The shared-mime-info type: the extension, and the first line of what is in it.
MIME_XML = """<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-mems-sketch">
    <comment>MEMS Sketch file</comment>
    <sub-class-of type="application/x-yaml"/>
    <glob pattern="*.msketch"/>
    <magic priority="50">
      <match type="string" value="format: mems-sketch/" offset="0:512"/>
      <match type="string" value="kind: component" offset="0:512"/>
      <match type="string" value="kind: process" offset="0:512"/>
    </magic>
    <icon name="mems-sketch"/>
  </mime-type>
</mime-info>
"""
CONNECT_MS = 500  # how long a second start waits for the running app


def desktop_entry(command: str) -> str:
    """The ``.desktop`` file that opens ``.msketch`` files with ``command``."""
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=MEMS Sketch\n"
        "Comment=Parametric MEMS layout design\n"
        f"Exec={command} %f\n"
        "Icon=mems-sketch\n"
        "Categories=Engineering;Science;\n"
        f"MimeType={MIME_TYPE};\n"
        "Terminal=false\n"
    )


def launcher() -> str:
    """How to start this app: the AppImage, the bundled program, or the script."""
    if os.environ.get("APPIMAGE"):
        return _quoted(os.environ["APPIMAGE"])
    if getattr(sys, "frozen", False):
        return _quoted(sys.executable)
    found = shutil.which("mems-sketch")
    return _quoted(found) if found else f"{_quoted(sys.executable)} -m mems_sketch.gui"


def install_linux(share: Path | None = None, icon: bytes | None = None) -> list[Path]:
    """Register ``.msketch`` for this user (``share``: ``~/.local/share``): the MIME
    type, the desktop entry, the icon; then let the desktop know. The files written."""
    share = share or Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    files = {
        share / "mime" / "packages" / "mems-sketch.xml": MIME_XML.encode(),
        share / "applications" / "mems-sketch.desktop": desktop_entry(launcher()).encode(),
    }
    if icon is not None:
        files[share / "icons" / "hicolor" / "256x256" / "apps" / "mems-sketch.png"] = icon
    for path, data in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for tool, folder in (
        ("update-mime-database", share / "mime"),
        ("update-desktop-database", share / "applications"),
    ):
        if shutil.which(tool):
            subprocess.run([tool, str(folder)], check=False, capture_output=True)
    return list(files)


# -- one running app ------------------------------------------------------------------


def server_name() -> str:
    """The local socket the running app listens on: one per user."""
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - no user name to be had
        user = "user"
    return f"mems-sketch-{user}"


def forward(path: str, name: str | None = None) -> bool:
    """Hand ``path`` to the app already running, if there is one: True when it took it."""
    socket = QLocalSocket()
    socket.connectToServer(name or server_name())
    if not socket.waitForConnected(CONNECT_MS):
        return False
    socket.write(os.path.abspath(path).encode() + b"\n")
    socket.flush()
    socket.waitForBytesWritten(CONNECT_MS)
    socket.disconnectFromServer()
    return True


def listen(open_path: Callable[[str], None], name: str | None = None) -> QLocalServer:
    """Take files a later start hands over (:func:`forward`), with ``open_path``."""
    name = name or server_name()
    server = QLocalServer()
    if not server.listen(name):
        QLocalServer.removeServer(name)  # left over by an app that did not quit cleanly
        server.listen(name)

    def connected() -> None:
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()

            def read(socket: QLocalSocket = socket) -> None:
                for line in bytes(socket.readAll()).decode(errors="replace").splitlines():
                    if line.strip():
                        open_path(line.strip())

            socket.readyRead.connect(read)
            socket.disconnected.connect(socket.deleteLater)

    server.newConnection.connect(connected)
    return server


class OpenEvents(QObject):
    """The files macOS hands the app to open (QFileOpenEvent), to ``open_path``."""

    def __init__(self, open_path: Callable[[str], None]) -> None:
        super().__init__()
        self.open_path = open_path

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.FileOpen:
            path = event.file() or event.url().toLocalFile()
            if path:
                self.open_path(path)
            return True
        return super().eventFilter(watched, event)


def route(path: str, windows: list, make_window: Callable[[], object]) -> object:
    """Open ``path`` where it belongs: the window with its project (brought to the
    front, at the component's tab), else a new window. Returns the window."""
    from mems_sketch.storage.project_files import ProjectFormatError, project_folder

    try:
        folder = project_folder(path).resolve()
    except (ProjectFormatError, OSError):
        folder = None
    for window in windows:
        document = getattr(window, "document", None)
        here = getattr(document, "path", None)
        if folder is not None and here is not None and Path(here).resolve() == folder:
            window.show_file(path)
            window.showNormal()
            window.raise_()
            window.activateWindow()
            return window
    window = make_window()
    window.open_project(path)
    window.show()
    return window


def _quoted(path: str) -> str:
    return f'"{path}"' if " " in path else path
