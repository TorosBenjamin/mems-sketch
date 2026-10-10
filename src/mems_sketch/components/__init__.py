"""The built-in components: an ordinary component library shipped with the tool
(``builtin/``, a project folder without a top component), so both backends
build them like any other component (requirement CMP-2).

They are placed by their bare names (``anchor``, ``comb_drive``,
``serpentine_spring``) wherever no component of the project has that name,
and draw relative to the level they are placed on (``level``,
``level.anchor``).
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mems_sketch.core.user_component import ComponentDef

BUILTIN_FOLDER = Path(__file__).parent / "builtin"


@functools.cache
def builtin_components() -> dict[str, ComponentDef]:
    """The built-in components by name, read once from ``builtin/``."""
    from mems_sketch.storage.project_files import load_library

    return load_library("builtin", BUILTIN_FOLDER).components
