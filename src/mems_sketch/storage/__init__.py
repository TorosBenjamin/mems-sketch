"""Loading and saving projects.

``load`` accepts a project folder, its ``project.yaml``, a one-file project
document (``.json``, ``.xml``, ``.mat``, ``.yaml``: see ``storage/document.py``)
or a legacy ``*.mems`` design. ``save`` writes a project folder;
``save`` to a document's name (``design.json``) writes that document.
"""

from __future__ import annotations

from pathlib import Path

from mems_sketch.core.project import Project
from mems_sketch.storage.document import is_document, read_project, write_project
from mems_sketch.storage.legacy_sqlite import load_legacy
from mems_sketch.storage.project_files import load_library, load_project, save_project


def load(path: str | Path) -> Project:
    path = Path(path)
    if path.is_file() and path.suffix == ".mems":
        return load_legacy(path)
    if is_document(path):
        return read_project(path)
    return load_project(path)


def save(project: Project, folder: str | Path) -> Path:
    """Write a project folder, or a one-file document when ``folder`` names one."""
    if is_document(folder):
        return write_project(project, folder)
    return save_project(project, folder)


def is_copy(path: str | Path) -> bool:
    """Whether opening ``path`` gives a copy to save as a folder (a legacy design
    or a one-file document), rather than the project folder itself."""
    path = Path(path)
    return path.suffix == ".mems" or is_document(path)


__all__ = [
    "is_copy",
    "is_document",
    "load",
    "load_legacy",
    "load_library",
    "load_project",
    "save",
    "save_project",
]
