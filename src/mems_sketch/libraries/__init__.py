"""The libraries that ship with mems-sketch, one per domain (``mems/``…).

Each is an ordinary library folder. Every project can use them without a
path: a project's manifest lists them as ``mems: bundled`` (``m: bundled:mems``
under another name), with the version it was saved with (``bundled@1``), and a
project that does not list them has them anyway, under their own names.
"""

from __future__ import annotations

import copy
import functools
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mems_sketch.core.project import Library

BUNDLED_FOLDER = Path(__file__).parent


def bundled_names() -> list[str]:
    """The bundled libraries' names."""
    from mems_sketch.storage.project_files import is_project_folder

    return sorted(p.name for p in BUNDLED_FOLDER.iterdir() if p.is_dir() and is_project_folder(p))


@functools.cache
def _loaded(name: str) -> Library:
    from mems_sketch.storage.project_files import load_library

    library = load_library(name, BUNDLED_FOLDER / name)
    library.bundled = name
    return library


def bundled_library(name: str, alias: str | None = None) -> Library:
    """The bundled library ``name``, as a project's own copy (under ``alias``)."""
    if name not in bundled_names():
        raise KeyError(f"there is no bundled library '{name}'")
    library = copy.deepcopy(_loaded(name))
    library.name = alias or name
    return library
