"""Exporter plugin interface and discovery.

An exporter is any class with ``format_name``, ``file_extension`` and an
``export(project, geometry, path)`` method. Optionally it has:

- ``title``: the name shown in File › Export… (default: the format name);
- ``options``: the settings it takes (:class:`ExportOption`). The export
  dialog and ``mems-sketch-cli export --option`` are built from them, and
  their values are passed to ``export`` as keyword arguments;
- ``wants_context = True``: it is also given the component and its parameter
  values (``component=``, ``params=``), e.g. to write its points.

Exporters are found through the ``mems_sketch.exporters`` entry-point group
(see ``pyproject.toml``), so a new format can live in its own package, or be
registered at runtime with :func:`register_exporter`.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, ClassVar, Protocol

from mems_sketch.core.component import Geometry
from mems_sketch.core.project import Project
from mems_sketch.options import Option

ENTRY_POINT_GROUP = "mems_sketch.exporters"
ExportOption = Option  # an exporter's settings are options


class Exporter(Protocol):
    format_name: ClassVar[str]
    file_extension: ClassVar[str]

    def export(self, project: Project, geometry: Geometry, path: Path) -> None: ...


def title_of(cls: type[Exporter]) -> str:
    return getattr(cls, "title", None) or cls.format_name.upper()


def options_of(cls: type[Exporter]) -> tuple[ExportOption, ...]:
    return tuple(getattr(cls, "options", ()))


def resolve_options(cls: type[Exporter], given: Mapping[str, Any] | None) -> dict[str, Any]:
    """Every option of ``cls``: ``given`` values checked, defaults for the rest."""
    declared = {o.name: o for o in options_of(cls)}
    unknown = sorted(set(given or {}) - set(declared))
    if unknown:
        known = ", ".join(declared) or "none"
        raise ValueError(
            f"'{cls.format_name}' has no option {', '.join(repr(n) for n in unknown)} "
            f"(its options: {known})"
        )
    values = {name: o.default for name, o in declared.items()}
    for name, value in (given or {}).items():
        values[name] = declared[name].check(value)
    return values


_runtime: dict[str, type[Exporter]] = {}


def register_exporter(cls: type[Exporter]) -> type[Exporter]:
    _runtime[cls.format_name] = cls
    return cls


def available_exporters() -> dict[str, type[Exporter]]:
    from mems_sketch.export import document_formats, layout_formats

    # The built-in ones even from a source tree (or an install older than them).
    found: dict[str, type[Exporter]] = {
        cls.format_name: cls for cls in (*layout_formats.BUILTIN, *document_formats.BUILTIN)
    }
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        try:
            found[ep.name] = ep.load()
        except (ImportError, AttributeError):  # a stale or broken plugin: skip it
            continue
    found.update(_runtime)
    return found


def exporter_class(format_name: str) -> type[Exporter]:
    exporters = available_exporters()
    try:
        return exporters[format_name]
    except KeyError:
        known = ", ".join(sorted(exporters))
        raise KeyError(f"unknown export format '{format_name}' (available: {known})") from None


def get_exporter(format_name: str) -> Exporter:
    return exporter_class(format_name)()


def format_for(path: str | Path) -> str:
    """The format that writes files like ``path``, by its extension."""
    suffix = Path(path).suffix.lower()
    matches = [n for n, c in available_exporters().items() if c.file_extension == suffix]
    if not matches:
        raise ValueError(f"no exporter handles '{suffix}' files")
    return matches[0]


def export(
    project: Project,
    path: str | Path,
    format_name: str | None = None,
    geometry: Geometry | None = None,
    component: str | None = None,
    params: dict | None = None,
    options: Mapping[str, Any] | None = None,
) -> Path:
    """Export ``geometry`` (default: drawn project) to ``path``.

    The format is taken from ``format_name`` or, failing that, the file extension.
    ``component`` and ``params`` say what the geometry is (for formats that
    record it); ``geometry`` defaults to that component rendered with them.
    ``options`` are the format's settings (see :class:`ExportOption`); those
    not given take their defaults.
    """
    path = Path(path)
    cls = exporter_class(format_name or format_for(path))
    values = resolve_options(cls, options)
    exporter = cls()
    if geometry is None:
        geometry = project.render(component, params)
    if getattr(exporter, "wants_context", False):
        values.update(component=component, params=params)
    exporter.export(project, geometry, path, **values)
    return path
