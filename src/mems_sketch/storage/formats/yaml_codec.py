"""YAML: the project files' own format, for a whole document in one file."""

from __future__ import annotations

from typing import Any

from mems_sketch.storage import yaml_format
from mems_sketch.storage.formats import Codec, Matrix


def _plain(tree: Any) -> Any:
    if isinstance(tree, Matrix):
        return tree.tolist()
    if isinstance(tree, dict):
        return {k: _plain(v) for k, v in tree.items()}
    if isinstance(tree, list):
        return [_plain(v) for v in tree]
    return tree  # bytes are written as !!binary


def dump(tree: Any) -> bytes:
    return yaml_format.dump(_plain(tree)).encode("utf-8")


def load(data: bytes) -> Any:
    return yaml_format.load(data.decode("utf-8"))


CODEC = Codec("yaml", "YAML", (".yaml", ".yml"), dump, load)
