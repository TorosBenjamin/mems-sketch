"""MATLAB .mat (version 5/7, what ``save`` writes unless told ``-v7.3``).

What a tree becomes in MATLAB:

* a dict: a struct (``s.components.top``); a dict whose keys are not all
  valid field names (``comb/finger``, ``5/0``) becomes a struct with two
  fields, ``keys`` and ``values`` (cell arrays), marked by a third,
  ``mems_sketch_map`` (``containers.Map(s.keys, s.values)`` reads it)
* a list: a cell array, row-shaped
* a number: a ``double`` (whole numbers come back as ints)
* a boolean: ``logical``; a string: a ``char`` row; None: ``[]``
* a :class:`Matrix`: a ``double`` matrix (a polygon: N×2)
* bytes: a struct with one field, ``mems_sketch_bytes`` (``uint8``)

Reading and writing .mat files needs ``scipy`` (``pip install
'mems-sketch[matlab]'``); the other formats do not.
"""

from __future__ import annotations

import io
import re
from typing import Any

from mems_sketch.storage.formats import Codec, Matrix, MissingDependency

MAP_MARK, BYTES_MARK = "mems_sketch_map", "mems_sketch_bytes"
_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")  # a MATLAB field name


def _scipy():
    try:
        import numpy as np
        import scipy.io as sio
    except ImportError as exc:
        raise MissingDependency(".mat files need scipy: pip install 'mems-sketch[matlab]'") from exc
    return np, sio


def _encode(tree: Any, np) -> Any:
    if isinstance(tree, dict):
        if all(isinstance(k, str) and _FIELD.match(k) for k in tree) and MAP_MARK not in tree:
            return {k: _encode(v, np) for k, v in tree.items()}
        return {
            MAP_MARK: np.array([[True]]),
            "keys": _cell(list(tree), np),
            "values": _cell(list(tree.values()), np),
        }
    if isinstance(tree, list):
        return _cell(tree, np)
    if isinstance(tree, Matrix):
        return np.array(tree.rows, dtype=float).reshape(len(tree.rows), -1)
    if isinstance(tree, bytes):
        return {BYTES_MARK: np.frombuffer(tree, dtype=np.uint8).reshape(1, -1)}
    if tree is None:
        return np.zeros((0, 0))
    if isinstance(tree, bool):
        return np.array([[tree]])
    if isinstance(tree, int | float):
        return np.array([[float(tree)]])
    if isinstance(tree, str):
        return np.array(tree)
    raise TypeError(f"cannot write {type(tree).__name__} to a .mat file")


def _cell(values: list, np) -> Any:
    cell = np.empty((1, len(values)) if values else (0, 0), dtype=object)
    for index, value in enumerate(values):
        cell[0, index] = _encode(value, np)
    return cell


def _decode(value: Any, np, sio) -> Any:
    struct = sio.matlab.mat_struct
    if isinstance(value, struct):
        fields = {name: getattr(value, name) for name in value._fieldnames}
        if BYTES_MARK in fields and len(fields) == 1:
            return np.asarray(fields[BYTES_MARK], dtype=np.uint8).tobytes()
        if MAP_MARK in fields:
            keys = _decode(fields["keys"], np, sio)
            values = _decode(fields["values"], np, sio)
            return dict(zip(keys, values, strict=True))
        return {name: _decode(v, np, sio) for name, v in fields.items()}
    if not isinstance(value, np.ndarray):
        return value
    if value.dtype == object:
        if value.size == 1 and isinstance(value.flat[0], struct):
            return _decode(value.flat[0], np, sio)  # a struct, not a cell holding one
        return [_decode(v, np, sio) for v in value.flatten(order="F")]
    if np.issubdtype(value.dtype, np.str_):
        return "".join(value.flatten().tolist())
    if value.dtype == bool or (value.dtype == np.uint8 and value.size == 1):
        return bool(value.flat[0])
    if np.issubdtype(value.dtype, np.number):
        if value.size == 0:
            return None
        if value.size == 1:
            return _number(float(value.flat[0]))
        if value.ndim == 2:
            return Matrix.of(value.tolist())
    raise ValueError(f"cannot read a {value.dtype} array of shape {value.shape}")


def _number(value: float) -> int | float:
    return int(value) if value.is_integer() and abs(value) < 1e15 else value


def dump(tree: Any) -> bytes:
    np, sio = _scipy()
    if not isinstance(tree, dict) or not all(_FIELD.match(k) for k in tree):
        raise ValueError("a .mat file holds a dict whose keys are MATLAB variable names")
    out = io.BytesIO()
    sio.savemat(
        out,
        {k: _encode(v, np) for k, v in tree.items()},
        do_compression=True,
        long_field_names=True,
        oned_as="row",
    )
    return out.getvalue()


def load(data: bytes) -> Any:
    np, sio = _scipy()
    raw = sio.loadmat(
        io.BytesIO(data), squeeze_me=False, struct_as_record=False, chars_as_strings=True
    )
    return {k: _decode(v, np, sio) for k, v in raw.items() if not k.startswith("__")}


CODEC = Codec("mat", "MATLAB", (".mat",), dump, load)
