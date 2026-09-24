"""One-file documents, in any format of :mod:`mems_sketch.storage.formats`.

A **project document** holds a whole project: what its folder's files hold,
in one tree, with the imported files' content inline::

    format: mems-sketch/1
    name, top, libraries        as in project.yaml (library paths relative to the file)
    process                     as in process.yaml
    imports: {padframe: {file, cell, layers, data: <bytes>}}
    components: {top: {...}, comb/finger: {...}}   as the component files

It is made from the same pieces the folder is (``storage/project_files``), so
it follows the model without its own mapping, and converting a folder to a
document and back gives the same folder.

A **geometry document** is what a component evaluates to, for other tools to
read and to be imported as a component::

    format: mems-sketch-geometry/1
    component: comb
    unit: um
    parameters: {pitch: 20}
    layers:
      device:
        gds: [1, 0]
        polygons: [{hull: <matrix>, holes: [<matrix>, ...]}, ...]
    points: {tip: {x: 10, y: 5}}

Each polygon's outline and holes are separate rows of ``x y`` points (µm),
not closed (the last point is not repeated).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import klayout.db as kdb

from mems_sketch.core.component import DBU_UM, Geometry, to_dbu
from mems_sketch.core.project import Project
from mems_sketch.storage import formats
from mems_sketch.storage.formats import Matrix, rows
from mems_sketch.storage.project_files import (
    FORMAT,
    PROJECT_FILE,
    component_data,
    component_from_data,
    imported_from_data,
    imports_data,
    load_library,
    process_data,
    process_from_data,
)

GEOMETRY_FORMAT = "mems-sketch-geometry/1"
UNIT = "um"


class DocumentError(ValueError):
    pass


def is_document(path: str | Path) -> bool:
    """Whether ``path`` names a one-file document (by its suffix), rather than a
    project folder or its ``project.yaml``."""
    path = Path(path)
    return path.name != PROJECT_FILE and formats.codec_for(path) is not None


# -- projects ----------------------------------------------------------------------


def project_data(project: Project, folder: str | Path | None = None) -> dict[str, Any]:
    """The project as one tree; library paths relative to ``folder`` (the document's)."""
    data: dict[str, Any] = {"format": FORMAT, "name": project.name, "top": project.top}
    if project.libraries:
        data["libraries"] = {
            n: _library_path(lib.path, folder) for n, lib in project.libraries.items()
        }
    data["process"] = process_data(project.process)
    if project.imports:
        entries = imports_data(project)
        data["imports"] = {
            name: {**entry, "data": project.imports[name].data} for name, entry in entries.items()
        }
    data["components"] = {name: _without_name(d) for name, d in project.components.items()}
    return data


def project_from_data(data: Any, folder: str | Path | None = None) -> Project:
    """A project from its tree; libraries are found relative to ``folder``."""
    data = _plain(data)
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        found = data.get("format") if isinstance(data, dict) else None
        if found == GEOMETRY_FORMAT:
            raise DocumentError("this is a geometry file: import it as a component instead")
        raise DocumentError(f"not a {FORMAT} project document")
    base = Path(folder) if folder is not None else Path.cwd()
    try:
        return Project(
            name=str(data.get("name", "untitled")),
            process=process_from_data(data.get("process") or {}),
            components={
                str(n): component_from_data(str(n), d or {})
                for n, d in (data.get("components") or {}).items()
            },
            top=data.get("top"),
            libraries={
                n: load_library(n, p if Path(p).is_absolute() else base / p)
                for n, p in (data.get("libraries") or {}).items()
            },
            imports={
                n: imported_from_data(n, e, e.get("data") or b"")
                for n, e in (data.get("imports") or {}).items()
            },
        )
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError(f"not a valid project document: {exc}") from exc


def write_project(project: Project, path: str | Path) -> Path:
    """Write the project as one document (format from the suffix)."""
    path = Path(path)
    return formats.write(path, project_data(project, path.parent))


def read_project(path: str | Path) -> Project:
    path = Path(path)
    return project_from_data(formats.read(path), path.parent)


def _without_name(definition) -> dict[str, Any]:
    data = component_data(definition)
    data.pop("name", None)  # the key names it
    return data


def _library_path(path: Path | None, folder: str | Path | None) -> str:
    if path is None:
        raise DocumentError("a library without a folder cannot be written")
    if folder is None:
        return str(path.resolve())
    try:
        return os.path.relpath(path.resolve(), Path(folder).resolve()).replace(os.sep, "/")
    except ValueError:  # another drive on Windows
        return str(path.resolve())


def _plain(tree: Any) -> Any:
    """Matrices as lists (a file written in MATLAB has ``[1 0]`` where YAML has
    ``[1, 0]``); one row becomes one list."""
    if isinstance(tree, Matrix):
        values = tree.tolist()
        values = [[_whole(v) for v in row] for row in values]
        return values[0] if len(values) == 1 else values
    if isinstance(tree, dict):
        return {k: _plain(v) for k, v in tree.items()}
    if isinstance(tree, list):
        return [_plain(v) for v in tree]
    return tree


def _whole(value: float) -> int | float:
    return int(value) if value.is_integer() and abs(value) < 1e15 else value


# -- geometry ----------------------------------------------------------------------


def geometry_data(
    project: Project,
    geometry: Geometry,
    component: str | None = None,
    parameters: dict[str, Any] | None = None,
    points: dict[str, tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """A component's geometry as one tree (see the module docstring)."""
    layers: dict[str, Any] = {}
    for name, region in sorted(geometry.layers.items()):
        entry: dict[str, Any] = {}
        layer = project.layers.get(name)
        if layer is not None:
            entry["gds"] = [layer.gds_layer, layer.gds_datatype]
        entry["polygons"] = [_polygon_data(p) for p in region.each_merged()]
        layers[name] = entry
    data: dict[str, Any] = {"format": GEOMETRY_FORMAT}
    if component is not None:
        data["component"] = component
    data["unit"] = UNIT
    data["parameters"] = dict(parameters or {})
    data["layers"] = layers
    data["points"] = {n: {"x": x, "y": y} for n, (x, y) in (points or {}).items()}
    return data


def _polygon_data(polygon: kdb.Polygon) -> dict[str, Any]:
    def loop(points) -> Matrix:
        return Matrix.of([(p.x * DBU_UM, p.y * DBU_UM) for p in points])

    data: dict[str, Any] = {"hull": loop(polygon.each_point_hull())}
    holes = [loop(polygon.each_point_hole(h)) for h in range(polygon.holes())]
    if holes:
        data["holes"] = holes
    return data


def geometry_from_data(data: Any) -> tuple[Geometry, dict[str, tuple[int, int] | None]]:
    """The geometry in a geometry document, and each layer's GDS numbers (None
    where the document gives none)."""
    if not isinstance(data, dict) or data.get("format") != GEOMETRY_FORMAT:
        if isinstance(data, dict) and data.get("format") == FORMAT:
            raise DocumentError("this is a project file: open it instead of importing it")
        raise DocumentError(f"not a {GEOMETRY_FORMAT} document")
    if data.get("unit", UNIT) != UNIT:
        raise DocumentError(f"unit '{data.get('unit')}' is not supported (only {UNIT})")
    geometry = Geometry()
    numbers: dict[str, tuple[int, int] | None] = {}
    try:
        for name, entry in (data.get("layers") or {}).items():
            gds = entry.get("gds")
            if isinstance(gds, Matrix):
                gds = gds.rows[0]
            numbers[name] = (int(gds[0]), int(gds[1]) if len(gds) > 1 else 0) if gds else None
            region = geometry.region(name)
            polygons = entry.get("polygons") or []
            if isinstance(polygons, dict):  # one polygon, as MATLAB saves a 1×1 struct array
                polygons = [polygons]
            for polygon in polygons:
                if not isinstance(polygon, dict):
                    polygon = {"hull": polygon}  # a bare outline, e.g. an N×2 matrix
                shape = kdb.Polygon(_points(polygon["hull"]))
                for hole in polygon.get("holes") or []:
                    shape.insert_hole(_points(hole))
                region.insert(shape)
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError(f"not a valid geometry document: {exc}") from exc
    return geometry, numbers


def _points(value: Any) -> list[kdb.Point]:
    return [kdb.Point(to_dbu(x), to_dbu(y)) for x, y, *_ in rows(value)]


def read_geometry(
    path: str | Path,
) -> tuple[Geometry, dict[str, tuple[int, int] | None], str | None]:
    """A geometry document's geometry, layer numbers and component name."""
    data = formats.read(path)
    geometry, numbers = geometry_from_data(data)
    return geometry, numbers, data.get("component") if isinstance(data, dict) else None
