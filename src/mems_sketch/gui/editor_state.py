"""The editor state of a project: how it was being looked at, not what it is.

Open tabs, zoom and position per tab, selections, rulers, collapsed tree
items, hidden layers and trial values are kept in ``.mems-sketch/state.json``
inside the project folder, so they move with the project. The folder carries
its own ``.gitignore``, so git never sees it. A missing, damaged or outdated
file is ignored: the project then opens with default views.

Settings that belong to the user rather than the project (window layout,
canvas theme, the active tool) stay in the application settings.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

STATE_DIR = ".mems-sketch"
STATE_FILE = "state.json"
VERSION = 1


def state_path(project_folder: str | Path) -> Path:
    return Path(project_folder) / STATE_DIR / STATE_FILE


def load_state(project_folder: str | Path) -> dict[str, Any]:
    """The saved state, or an empty dict if there is none (or it cannot be used)."""
    try:
        data = json.loads(state_path(project_folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return {}
    return data


def save_state(project_folder: str | Path, state: dict[str, Any]) -> Path:
    """Write the state atomically, creating the folder and its ``.gitignore``."""
    path = state_path(project_folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    ignore = path.parent / ".gitignore"
    if not ignore.exists():
        ignore.write_text("# Editor state of MEMS Sketch: local, not part of the project\n*\n")
    text = json.dumps({**state, "version": VERSION}, indent=1, sort_keys=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path
