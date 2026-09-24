"""Geometry as JSON, XML or MATLAB .mat: a geometry document (see
:mod:`mems_sketch.storage.document`) in the format of
:mod:`mems_sketch.storage.formats` with the same name. One class serves every
format, so a new format needs only a codec and a line here."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from mems_sketch.core.compiler import Compiler
from mems_sketch.core.component import Geometry
from mems_sketch.core.project import Project
from mems_sketch.storage import formats
from mems_sketch.storage.document import geometry_data


class DocumentExporter:
    format_name: ClassVar[str]
    file_extension: ClassVar[str]
    wants_context: ClassVar[bool] = True  # gets the component and its parameters too

    def export(
        self,
        project: Project,
        geometry: Geometry,
        path: Path,
        component: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        name = component or project.default_component()
        parameters: dict[str, Any] = {}
        points: dict[str, tuple[float, float]] = {}
        if name is not None:
            session = Compiler().session(project)
            variables = session.variables(name, params)
            parameters = {k: v for k, v in variables.items() if k not in session.scope}
            points = {n: (x, y) for n, (x, y) in session.points(name, params).items()}
        tree = geometry_data(project, geometry, name, parameters, points)
        Path(path).write_bytes(formats.codecs()[self.format_name].dump(tree))


class JsonExporter(DocumentExporter):
    format_name = "json"
    file_extension = ".json"


class XmlExporter(DocumentExporter):
    format_name = "xml"
    file_extension = ".xml"


class MatExporter(DocumentExporter):
    format_name = "mat"
    file_extension = ".mat"


BUILTIN = (JsonExporter, XmlExporter, MatExporter)
