"""Loading and saving projects.

``load`` accepts a project folder, its ``project.yaml``, or a legacy
single-file ``*.mems`` design; ``save`` always writes a project folder.
"""

from __future__ import annotations

from pathlib import Path

from mems_sketch.core.project import Project
from mems_sketch.storage.legacy_sqlite import load_legacy
from mems_sketch.storage.project_files import load_library, load_project, save_project


def load(path: str | Path) -> Project:
    path = Path(path)
    if path.is_file() and path.suffix == ".mems":
        return load_legacy(path)
    return load_project(path)


def save(project: Project, folder: str | Path) -> Path:
    return save_project(project, folder)


__all__ = ["load", "load_library", "load_legacy", "load_project", "save", "save_project"]
