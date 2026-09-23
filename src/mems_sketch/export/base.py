"""Exporter plugin interface and discovery.

An exporter is any class with ``format_name``, ``file_extension`` and an
``export(design, geometry, path)`` method. Exporters are found through the
``mems_sketch.exporters`` entry-point group (see ``pyproject.toml``), so a new
format can live in its own package, or be registered at runtime with
:func:`register_exporter`.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path
from typing import ClassVar, Protocol

from mems_sketch.core.component import Geometry
from mems_sketch.core.design import Design

ENTRY_POINT_GROUP = "mems_sketch.exporters"


class Exporter(Protocol):
    format_name: ClassVar[str]
    file_extension: ClassVar[str]

    def export(self, design: Design, geometry: Geometry, path: Path) -> None: ...


_runtime: dict[str, type[Exporter]] = {}


def register_exporter(cls: type[Exporter]) -> type[Exporter]:
    _runtime[cls.format_name] = cls
    return cls


def available_exporters() -> dict[str, type[Exporter]]:
    found: dict[str, type[Exporter]] = {}
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        found[ep.name] = ep.load()
    if not found:  # running from a source tree without an installed package
        from mems_sketch.export import klayout_formats

        for cls in klayout_formats.BUILTIN:
            found[cls.format_name] = cls
    found.update(_runtime)
    return found


def get_exporter(format_name: str) -> Exporter:
    exporters = available_exporters()
    try:
        return exporters[format_name]()
    except KeyError:
        known = ", ".join(sorted(exporters))
        raise KeyError(f"unknown export format '{format_name}' (available: {known})") from None


def export(
    design: Design,
    path: str | Path,
    format_name: str | None = None,
    geometry: Geometry | None = None,
) -> Path:
    """Export ``geometry`` (default: drawn design) to ``path``.

    The format is taken from ``format_name`` or, failing that, the file extension.
    """
    path = Path(path)
    if format_name is None:
        suffix = path.suffix.lower()
        matches = [n for n, c in available_exporters().items() if c.file_extension == suffix]
        if not matches:
            raise ValueError(f"no exporter handles '{suffix}' files")
        format_name = matches[0]
    get_exporter(format_name).export(
        design, design.render() if geometry is None else geometry, path
    )
    return path
