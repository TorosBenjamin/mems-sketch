"""Design files as SQLite databases (``*.mems``).

The file holds the parametric model only (layers, variables, user-defined
components, instances), never generated geometry. Values that may be expressions are stored as JSON so that
numbers and expression strings round-trip unchanged.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from mems_sketch.core.design import Design, Instance, Layer
from mems_sketch.core.user_component import ComponentDef

SCHEMA_VERSION = 2
READABLE_VERSIONS = {1, 2}  # version 1 had no components table

_SCHEMA = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE layers (
    name         TEXT PRIMARY KEY,
    gds_layer    INTEGER NOT NULL,
    gds_datatype INTEGER NOT NULL DEFAULT 0,
    undercut     REAL NOT NULL DEFAULT 0,
    min_width    REAL,
    min_space    REAL
);
CREATE TABLE variables (
    name  TEXT PRIMARY KEY,
    value TEXT NOT NULL            -- JSON: number or expression string
);
CREATE TABLE components (
    position   INTEGER NOT NULL,
    name       TEXT PRIMARY KEY,
    definition TEXT NOT NULL        -- JSON ComponentDef
);
CREATE TABLE instances (
    position  INTEGER NOT NULL,     -- keeps instance order stable
    name      TEXT PRIMARY KEY,
    component TEXT NOT NULL,
    params    TEXT NOT NULL,        -- JSON object
    x         TEXT NOT NULL,        -- JSON: number or expression string
    y         TEXT NOT NULL,
    rotation  TEXT NOT NULL,
    mirror_x  INTEGER NOT NULL DEFAULT 0
);
"""


def save(design: Design, path: str | Path) -> None:
    """Write ``design`` to ``path``.

    The file is written next to the target and then renamed over it, so an
    interrupted save never leaves a half-written design behind.
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    with conn:
        conn.executescript(_SCHEMA)
        conn.executemany(
            "INSERT INTO meta VALUES (?, ?)",
            [("schema_version", str(SCHEMA_VERSION)), ("name", design.name)],
        )
        conn.executemany(
            "INSERT INTO layers VALUES (?, ?, ?, ?, ?, ?)",
            [
                (ly.name, ly.gds_layer, ly.gds_datatype, ly.undercut, ly.min_width, ly.min_space)
                for ly in design.layers.values()
            ],
        )
        conn.executemany(
            "INSERT INTO variables VALUES (?, ?)",
            [(name, json.dumps(value)) for name, value in design.variables.items()],
        )
        conn.executemany(
            "INSERT INTO components VALUES (?, ?, ?)",
            [
                (i, definition.name, definition.model_dump_json())
                for i, definition in enumerate(design.components.values())
            ],
        )
        conn.executemany(
            "INSERT INTO instances VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    i,
                    inst.name,
                    inst.component,
                    json.dumps(inst.params),
                    json.dumps(inst.x),
                    json.dumps(inst.y),
                    json.dumps(inst.rotation),
                    int(inst.mirror_x),
                )
                for i, inst in enumerate(design.instances)
            ],
        )
    conn.close()
    os.replace(tmp, path)


def load(path: str | Path) -> Design:
    if not Path(path).is_file():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(path)
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta"))
        version = int(meta.get("schema_version", 0))
        if version not in READABLE_VERSIONS:
            raise ValueError(f"unsupported design file version {version}")
        design = Design(name=meta.get("name", "untitled"))
        for row in conn.execute("SELECT * FROM layers"):
            design.add_layer(Layer(*row))
        for name, value in conn.execute("SELECT name, value FROM variables"):
            design.variables[name] = json.loads(value)
        if version >= 2:
            for (definition,) in conn.execute(
                "SELECT definition FROM components ORDER BY position"
            ):
                parsed = ComponentDef.model_validate_json(definition)
                design.components[parsed.name] = parsed
        rows = conn.execute(
            "SELECT name, component, params, x, y, rotation, mirror_x FROM instances ORDER BY position"
        )
        for name, component, params, x, y, rotation, mirror_x in rows:
            design.instances.append(
                Instance(
                    name=name,
                    component=component,
                    params=json.loads(params),
                    x=json.loads(x),
                    y=json.loads(y),
                    rotation=json.loads(rotation),
                    mirror_x=bool(mirror_x),
                )
            )
        return design
    finally:
        conn.close()
