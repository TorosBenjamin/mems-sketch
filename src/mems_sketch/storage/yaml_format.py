"""Canonical YAML for models: stable, minimal and diff-friendly.

* Fields equal to their default are left out (except a node's ``kind``).
* Keys keep a fixed order: ``kind`` and ``name`` first,
  ``modifiers``, ``align`` and ``enabled`` last, everything else in declaration order.
* Whole numbers are written without ``.0``; lists of plain values (points,
  GDS numbers) are written on one line.

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


_Dumper.add_representer(list, _represent_list)


def dump(data: Any) -> str:
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)


def load(text: str) -> Any:
    return yaml.safe_load(text)
