"""Components as written: for reading, with everything named a map by its name.

A component (``components/top/component.yaml``, or an entry of a one-file
document)::

    description: Comb-driven resonator
    level: poly1
    parameters:
      plate: 160                                  # only a default
      w_finger: {default: process.min_gap, min: 1}
    points:
      tip: {at: beam.right, x: 2}
    private:
      finger: finger                              # its folder, relative to this one
    shapes:
      mass: {ref: std.perforated_plate, size: plate, pitch: pitch}
      comb_top:
        ref: comb_drive
        fingers: 16
        rotation: 180
        align: {point: moving, to: mass.top, dy: -1}
      slot:
        kind: boolean
        op: subtract
        a:
          plate: {kind: rect, x1: 10, y1: 10}
        b:
          hole: {kind: circle, r: 2}

A placement is ``ref:`` its component, then its parameter values, then its own
fields (position, rotation, level...). When a parameter's name is also one of
those fields, all its values go under ``params:`` instead. Shapes that hold
shapes (a boolean's ``a`` and ``b``, a transform's ``children``) hold maps too.
Every shape has a name (see :func:`mems_sketch.core.user_component.name_shapes`).
"""

from __future__ import annotations

from typing import Any

from mems_sketch.core.shapes import RefShape, Shape, walk
from mems_sketch.core.shapes.registry import kind_class
from mems_sketch.core.user_component import ComponentDef, name_shapes
from mems_sketch.storage import yaml_format

PLACEMENT_FIELDS = frozenset(RefShape.model_fields) | {"ref", "params"}


def component_data(definition: ComponentDef, private: dict[str, Any] | None = None) -> dict:
    """A component as written. ``private`` gives its private components by short
    name: their folders (a project folder) or their own data (a document)."""
    if any(shape.name is None for shape in walk(definition.shapes)):
        definition = definition.model_copy(deep=True)
        name_shapes(definition.shapes)
    data: dict[str, Any] = {}
    if definition.description:
        data["description"] = definition.description
    if definition.level is not None:
        data["level"] = definition.level
    if definition.parameters:
        data["parameters"] = {p.name: _parameter_data(p) for p in definition.parameters}
    if definition.points:
        data["points"] = {p.name: _without_name(p) for p in definition.points}
    if private:
        data["private"] = dict(private)
    if definition.shapes:
        data["shapes"] = shapes_data(definition.shapes)
    if definition.waivers:
        data["waivers"] = yaml_format.to_data(definition.waivers)
    return data


def component_from_data(name: str, data: dict[str, Any]) -> tuple[ComponentDef, dict[str, Any]]:
    """A component named ``name`` (its path: ``comb/finger``) from its data, and
    the entries of its private components, by short name, as written."""
    if not isinstance(data, dict):
        raise ValueError("a component is a map of its fields")  # noqa: TRY004 - a file error
    unknown = set(data) - {
        "description",
        "level",
        "parameters",
        "points",
        "private",
        "shapes",
        "waivers",
    }
    if unknown:
        raise ValueError(f"unknown field '{min(unknown)}'")
    parameters = []
    for parameter, value in _map(data, "parameters").items():
        fields = value if isinstance(value, dict) else {"default": value}
        parameters.append({"name": str(parameter), **fields})
    model = {
        "name": name,
        "description": data.get("description", ""),
        "level": data.get("level"),
        "parameters": parameters,
        "points": [{"name": str(n), **(v or {})} for n, v in _map(data, "points").items()],
        "shapes": shapes_from_data(_map(data, "shapes")),
        "waivers": data.get("waivers") or [],
    }
    private = {str(k): v for k, v in _map(data, "private").items()}
    return ComponentDef.model_validate(model), private


def shapes_data(shapes: list[Shape]) -> dict[str, Any]:
    return {shape.name: _shape_data(shape) for shape in shapes}


def shapes_from_data(entries: dict[str, Any]) -> list[dict[str, Any]]:
    """Shapes as the model reads them (plain data), from a map of them by name."""
    return [_shape_from_data(str(name), entry) for name, entry in entries.items()]


def _shape_data(shape: Shape) -> dict[str, Any]:
    data = yaml_format.to_data(shape)
    data.pop("name", None)
    for field in type(shape).child_fields:
        if field in data:
            data[field] = shapes_data(getattr(shape, field))
    if not isinstance(shape, RefShape):
        return data
    data.pop("kind")
    params = data.pop("params", {})
    result: dict[str, Any] = {"ref": data.pop("component")}
    if set(params) & PLACEMENT_FIELDS:
        result["params"] = params
    else:
        result.update(params)
    result.update(data)
    return result


def _shape_from_data(name: str, entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError(f"shape '{name}' is not a map of its fields")  # noqa: TRY004
    entry = dict(entry)
    if "ref" in entry:
        data: dict[str, Any] = {"kind": "ref", "name": name, "component": entry.pop("ref")}
        params = dict(entry.pop("params", None) or {})
        for key, value in entry.items():
            if key in RefShape.model_fields:
                data[key] = value
            else:
                params[key] = value
        data["params"] = params
        return data
    if "kind" not in entry:
        raise ValueError(f"shape '{name}' has neither a kind nor a ref")
    for field in kind_class(str(entry["kind"])).child_fields:
        if field in entry:
            entry[field] = shapes_from_data(_map(entry, field, f"'{field}' of shape '{name}'"))
    return {"name": name, **entry}


def _parameter_data(parameter) -> Any:
    fields = _without_name(parameter)
    if set(fields) <= {"default"}:
        return fields.get("default", 0)
    return fields


def _without_name(model) -> dict[str, Any]:
    data = yaml_format.to_data(model)
    data.pop("name", None)
    return data


def _map(data: dict[str, Any], key: str, what: str | None = None) -> dict[str, Any]:
    value = data.get(key) or {}
    if not isinstance(value, dict):
        raise ValueError(f"{what or repr(key)} is a map by name, not a list")  # noqa: TRY004
    return value
