"""Import of the earlier single-file SQLite design format (``*.mems``, schema v1-v3).

These files are read-only now; open one and save it as a project folder.
Global variables become parameters of the top component, and the top-level
shapes (or v1/v2 instances) become its shapes. The retired ``rectangle``
component becomes a ``rect`` in a transform where it was placed.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pydantic import TypeAdapter

from mems_sketch.core.process import Layer, layer_rules
from mems_sketch.core.project import Project
from mems_sketch.core.shapes import RectShape, RefShape, Shape, TransformShape
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
        rows = conn.execute("SELECT * FROM layers")
        columns = [c[0] for c in rows.description]
        known = set(Layer.__dataclass_fields__)
        for row in rows:  # columns Layer no longer has (e.g. undercut) are left out
            values = dict(zip(columns, row))
            project.add_layer(Layer(**{k: v for k, v in values.items() if k in known}))
            for rule in layer_rules(
                values["name"], values.get("min_width"), values.get("min_space")
            ):
                project.process.add_rule(rule)
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
        for definition in project.components.values():
            definition.shapes = [_without_rectangles(shape) for shape in definition.shapes]
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


def _without_rectangles(shape: Shape) -> Shape:
    """``shape`` with every placement of the retired ``rectangle`` component (a
    centred width × height rectangle on a layer) made a rect in a transform."""
    if isinstance(shape, RefShape) and shape.component == "rectangle":
        params = shape.params
        w, h = params.get("width", 100.0), params.get("height", 50.0)
        half_w, half_h = (f"({v}) / 2" if isinstance(v, str) else v / 2 for v in (w, h))
        negative = (f"-{v}" if isinstance(v, str) else -v for v in (half_w, half_h))
        x0, y0 = negative
        rect = RectShape(
            layer=str(params.get("layer", "device")), x0=x0, y0=y0, x1=half_w, y1=half_h
        )
        return TransformShape(
            name=shape.name,
            children=[rect],
            x=shape.x,
            y=shape.y,
            rotation=shape.rotation,
            mirror_x=shape.mirror_x,
            modifiers=shape.modifiers,
            align=shape.align,
            enabled=shape.enabled,
        )
    fields = type(shape).child_fields
    if not fields:
        return shape
    return shape.model_copy(
        update={f: [_without_rectangles(child) for child in getattr(shape, f)] for f in fields}
    )
