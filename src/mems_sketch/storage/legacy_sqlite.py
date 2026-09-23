"""Import of the earlier single-file SQLite design format (``*.mems``, schema v1-v3).

These files are read-only now; open one and save it as a project folder.
Global variables become parameters of the top component, and the top-level
shapes (or v1/v2 instances) become its shapes.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pydantic import TypeAdapter

from mems_sketch.core.process import Layer
from mems_sketch.core.project import Project
from mems_sketch.core.shapes import RefShape, Shape
from mems_sketch.core.user_component import ComponentDef, ParamDef

READABLE_VERSIONS = {1, 2, 3}
_SHAPE = TypeAdapter(Shape)


def load_legacy(path: str | Path) -> Project:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta"))
        version = int(meta.get("schema_version", 0))
        if version not in READABLE_VERSIONS:
            raise ValueError(f"unsupported design file version {version}")
        project = Project(name=meta.get("name", path.stem))
        for row in conn.execute("SELECT * FROM layers"):
            project.add_layer(Layer(*row))
        if version >= 2:
            for (definition,) in conn.execute(
                "SELECT definition FROM components ORDER BY position"
            ):
                parsed = ComponentDef.model_validate_json(definition)
                project.components[parsed.name] = parsed
        top = project.top_component
        for name, value in conn.execute("SELECT name, value FROM variables"):
            top.parameters.append(ParamDef(name=name, default=json.loads(value)))
        if version >= 3:
            rows = conn.execute("SELECT definition FROM shapes ORDER BY position")
            top.shapes.extend(_SHAPE.validate_json(definition) for (definition,) in rows)
        else:
            top.shapes.extend(_legacy_instances(conn))
        return project
    finally:
        conn.close()


def _legacy_instances(conn: sqlite3.Connection) -> list[Shape]:
    rows = conn.execute(
        "SELECT name, component, params, x, y, rotation, mirror_x FROM instances ORDER BY position"
    )
    return [
        RefShape(
            name=name,
            component=component,
            params=json.loads(params),
            x=json.loads(x),
            y=json.loads(y),
            rotation=json.loads(rotation),
            mirror_x=bool(mirror_x),
        )
        for name, component, params, x, y, rotation, mirror_x in rows
    ]
