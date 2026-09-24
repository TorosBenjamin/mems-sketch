"""Exporter plugin interface and discovery.

An exporter is any class with ``format_name``, ``file_extension`` and an
``export(project, geometry, path)`` method. One with ``wants_context = True``
is also given the component and its parameter values (``component=``,
``params=``), e.g. to write its points. Exporters are found through the
``mems_sketch.exporters`` entry-point group (see ``pyproject.toml``), so a new
format can live in its own package, or be registered at runtime with
:func:`register_exporter`.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path
from typing import ClassVar, Protocol

from mems_sketch.core.component import Geometry
from mems_sketch.core.project import Project

ENTRY_POINT_GROUP = "mems_sketch.exporters"


class Exporter(Protocol):
    format_name: ClassVar[str]
    file_extension: ClassVar[str]

    def export(self, project: Project, geometry: Geometry, path: Path) -> None: ...


_runtime: dict[str, type[Exporter]] = {}


def register_exporter(cls: type[Exporter]) -> type[Exporter]:
    _runtime[cls.format_name] = cls
    return cls


def available_exporters() -> dict[str, type[Exporter]]:
    from mems_sketch.export import document_formats, klayout_formats

    # The built-in ones even from a source tree (or an install older than them).
    found: dict[str, type[Exporter]] = {
        cls.format_name: cls for cls in (*klayout_formats.BUILTIN, *document_formats.BUILTIN)
    }
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        try:
            found[ep.name] = ep.load()
        except (ImportError, AttributeError):  # a stale or broken plugin: skip it
            continue
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
    project: Project,
    path: str | Path,
    format_name: str | None = None,
    geometry: Geometry | None = None,
    component: str | None = None,
    params: dict | None = None,
) -> Path:
    """Export ``geometry`` (default: drawn project) to ``path``.

    The format is taken from ``format_name`` or, failing that, the file extension.
    ``component`` and ``params`` say what the geometry is (for formats that
    record it); ``geometry`` defaults to that component rendered with them.
    """
    path = Path(path)
    if format_name is None:
        suffix = path.suffix.lower()
        matches = [n for n, c in available_exporters().items() if c.file_extension == suffix]
        if not matches:
            raise ValueError(f"no exporter handles '{suffix}' files")
        format_name = matches[0]
    exporter = get_exporter(format_name)
    if geometry is None:
        geometry = project.render(component, params)
    if getattr(exporter, "wants_context", False):
        exporter.export(project, geometry, path, component=component, params=params)
    else:
        exporter.export(project, geometry, path)
    return path
