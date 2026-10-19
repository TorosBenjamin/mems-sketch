"""The shape kinds and modifiers, described for an assistant from the models:
what each is (its module's and class's documentation) and its fields, with
readable types and their defaults."""

from __future__ import annotations

import inspect
import types
import typing
from typing import Any

from pydantic import BaseModel

from mems_sketch.core.shapes import KINDS
from mems_sketch.core.shapes.modifiers import MODIFIER_KINDS
from mems_sketch.storage import yaml_format

KINDS_PACKAGE = "mems_sketch.core.shapes.kinds."
# Fields every shape has; the components reference topic explains them.
COMMON = ("name", "modifiers", "align", "enabled")
CATEGORIES = {
    "primitive": "drawn shapes",
    "reference": "placing components and joining or wiring them",
    "operation": "shapes made from other shapes (their children)",
    "guide": "construction",
}


def index() -> str:
    """Every kind and modifier, one line each."""
    lines = ["# Shape kinds", ""]
    for category, about in CATEGORIES.items():
        lines.append(f"{category.capitalize()}: {about}")
        lines += [f"- `{k.kind_name()}`: {_first_line(k)}" for k in KINDS if k.category == category]
        lines.append("")
    lines += ["# Modifiers (a shape's `modifiers` stack)", ""]
    lines += [f"- `{m.kind_name()}`: {_first_line(m)}" for m in MODIFIER_KINDS]
    lines += ["", "describe_kind(kind) gives one's fields; reference(topic) the format and names."]
    return "\n".join(lines)


def describe(kind: str) -> str:
    """What ``kind`` (a shape kind or modifier) is, and its fields."""
    models = {k.kind_name(): k for k in (*KINDS, *MODIFIER_KINDS)}
    model = models.get(kind)
    if model is None:
        raise ValueError(f"there is no kind '{kind}': there are {', '.join(models)}")
    modifier = model in MODIFIER_KINDS
    what = "modifier" if modifier else f"{model.category} shape"
    lines = [f"# {kind} ({what})", "", _about(model), "", "Fields:"]
    for name, info in model.model_fields.items():
        if name == "kind" or (not modifier and name in COMMON):
            continue
        lines.append(f"- `{name}`: {_field(name, info)}")
        nested = _model_in(info.annotation)
        if nested is not None:
            for sub, sub_info in nested.model_fields.items():
                lines.append(f"  - `{sub}`: {_field(sub, sub_info)}")
    if not modifier:
        lines += [
            "",
            f"Every shape also has {', '.join(f'`{c}`' for c in COMMON)}: see reference('components').",
        ]
    example = _example(model)
    if example:
        lines += ["", "Example:", "```yaml", example.rstrip(), "```"]
    return "\n".join(lines)


def _about(model) -> str:
    """The module's documentation (a kind's own module) and the class's own."""
    parts = []
    module = inspect.getmodule(model)
    if module is not None and module.__name__.startswith(KINDS_PACKAGE) and module.__doc__:
        parts.append(inspect.cleandoc(module.__doc__))
    own = model.__dict__.get("__doc__")
    if own and not any(inspect.cleandoc(own) in p for p in parts):
        parts.append(inspect.cleandoc(own))
    text = "\n\n".join(parts) or "(no description)"
    return text.replace("``", "`")


def _first_line(model) -> str:
    return _about(model).split("\n\n")[0].replace("\n", " ").rstrip(".")


def _field(name: str, info) -> str:
    text = "layer (see reference('layers'))" if name == "layer" else _type(info.annotation)
    if info.is_required():
        text += ", required"
    else:
        default = info.get_default(call_default_factory=True)
        if default not in (None, [], {}):
            text += f", default {_value(default)}"
    if info.description:
        text += f": {info.description}"
    return text


def _type(annotation) -> str:
    """A readable type: ``number or expression``, ``one of a, b``, ``list of [x, y]``."""
    origin, args = typing.get_origin(annotation), typing.get_args(annotation)
    if origin is typing.Annotated:
        return _type(args[0])
    if origin is typing.Literal:
        return "one of " + ", ".join(str(a) for a in args)
    if origin in (typing.Union, types.UnionType):
        rest = [a for a in args if a is not type(None)]
        if set(rest) == {float, str}:
            text = "number or expression"
        elif len(rest) == 1:
            text = _type(rest[0])
        elif all(isinstance(a, type) and issubclass(a, BaseModel) for a in rest):
            return "shape"
        else:
            text = " or ".join(_type(a) for a in rest)
        return text + (", or empty" if type(None) in args else "")
    if origin is list:
        inner = _type(args[0]) if args else "values"
        return "shapes (a map by name)" if inner == "shape" else f"list of {inner}"
    if origin is tuple:
        return "[x, y]" if len(args) == 2 else "[" + ", ".join(_type(a) for a in args) + "]"
    if origin is dict:
        return f"map of {_type(args[0])} to {_type(args[1])}"
    if annotation is bool:
        return "true or false"
    if annotation is int:
        return "whole number"
    if annotation is float:
        return "number"
    if annotation is str:
        return "text"
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return "{" + ", ".join(annotation.model_fields) + "}"
    return str(annotation).replace("typing.", "")


def _model_in(annotation) -> type[BaseModel] | None:
    """A model a field holds (a list of corners), to list its fields too."""
    for candidate in (annotation, *typing.get_args(annotation)):
        is_model = isinstance(candidate, type) and issubclass(candidate, BaseModel)
        if is_model and candidate.__module__.startswith("mems_sketch.core.shapes.modifiers"):
            return candidate
    return None


def _value(value: Any) -> str:
    data = yaml_format.to_data(value)
    if isinstance(data, bool):
        return "true" if data else "false"
    if isinstance(data, float) and data.is_integer():
        return str(int(data))
    return str(data)


def _example(model) -> str:
    """The kind's default shape, as the file format writes it, when it has one."""
    if model in MODIFIER_KINDS:
        try:
            modifier = model.new()
        except (TypeError, ValueError, AttributeError):
            return ""
        data = yaml_format.to_data(modifier)
        return yaml_format.dump({"modifiers": [data]}) if set(data) - {"kind"} else ""
    try:
        shape = model.default("device")
    except (TypeError, ValueError, AttributeError):
        return ""
    from mems_sketch.storage.component_format import shapes_data

    data = shapes_data([shape.model_copy(update={"name": "example"})])
    return yaml_format.dump(data) if set(data["example"]) - {"kind"} else ""
