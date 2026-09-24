"""JSON. Bytes are written as ``{"base64": "..."}``; a matrix as a list of rows."""

from __future__ import annotations

import base64
import json
from typing import Any

from mems_sketch.storage.formats import Codec, Matrix

BYTES_KEY = "base64"


def _encode(tree: Any) -> Any:
    if isinstance(tree, Matrix):
        return tree.tolist()
    if isinstance(tree, bytes):
        return {BYTES_KEY: base64.b64encode(tree).decode("ascii")}
    if isinstance(tree, dict):
        return {k: _encode(v) for k, v in tree.items()}
    if isinstance(tree, list):
        return [_encode(v) for v in tree]
    return tree


def _decode(tree: Any) -> Any:
    if isinstance(tree, dict):
        if set(tree) == {BYTES_KEY} and isinstance(tree[BYTES_KEY], str):
            return base64.b64decode(tree[BYTES_KEY])
        return {k: _decode(v) for k, v in tree.items()}
    if isinstance(tree, list):
        return [_decode(v) for v in tree]
    return tree


def dump(tree: Any) -> bytes:
    return (json.dumps(_encode(tree), indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def load(data: bytes) -> Any:
    return _decode(json.loads(data.decode("utf-8")))


CODEC = Codec("json", "JSON", (".json",), dump, load)
