"""Canonical YAML for models: stable, minimal and diff-friendly.

* Fields equal to their default are left out (except a node's ``kind``).
* Keys keep a fixed order: ``kind`` and ``name`` first,
  ``modifiers``, ``align`` and ``enabled`` last, everything else in declaration order.
* Whole numbers are written without ``.0``; lists of plain values (points,
  GDS numbers), and short maps of plain values (an alignment, a placement's
  values), are written on one line.

Saving the same model twice gives byte-identical output, so a change in the
model shows up as a small, readable diff.
"""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import BaseModel

_FIRST = ("kind", "name")
_LAST = ("modifiers", "align", "enabled")


def to_data(value: Any) -> Any:
    """Plain Python data (dicts, lists, str, numbers) for a model or value."""
    if isinstance(value, BaseModel):
        return _model_data(value)
    if isinstance(value, list | tuple):
        return [to_data(v) for v in value]
    if isinstance(value, dict):
        return {k: to_data(v) for k, v in value.items()}
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return int(value)
    return value


def _model_data(model: BaseModel) -> dict[str, Any]:
    fields = type(model).model_fields
    names = [n for n in _FIRST if n in fields]
    names += [n for n in fields if n not in _FIRST and n not in _LAST]
    names += [n for n in _LAST if n in fields]
    data: dict[str, Any] = {}
    for name in names:
        value = getattr(model, name)
        if name != "kind" and value == fields[name].get_default(call_default_factory=True):
            continue
        data[name] = to_data(value)
    return data


class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:
        return True


def _represent_list(dumper: yaml.SafeDumper, data: list) -> yaml.Node:
    flat = all(not isinstance(v, dict | list) for v in data)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=flat)


FLOW_WIDTH = 72  # a map of plain values this short (as text) is written on one line


def _represent_dict(dumper: yaml.SafeDumper, data: dict) -> yaml.Node:
    plain = all(
        not isinstance(v, dict | list) or (isinstance(v, list) and _flat(v)) for v in data.values()
    )
    short = sum(len(str(k)) + len(str(v)) + 4 for k, v in data.items()) <= FLOW_WIDTH
    flow = bool(data) and plain and short and all(_plain_text(v) for v in data.values())
    return dumper.represent_mapping("tag:yaml.org,2002:map", data, flow_style=flow)


def _flat(values: list) -> bool:
    return all(not isinstance(v, dict | list) and _plain_text(v) for v in values)


def _plain_text(value: Any) -> bool:
    """Whether a value reads the same in a one-line map (no line breaks or ``#``)."""
    return not isinstance(value, str) or ("\n" not in value and "#" not in value)


_Dumper.add_representer(list, _represent_list)
_Dumper.add_representer(dict, _represent_dict)


def dump(data: Any) -> str:
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)


def load(text: str) -> Any:
    return yaml.safe_load(text)
