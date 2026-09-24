"""File formats for plain data: YAML, JSON, XML and MATLAB .mat.

Everything the app writes to a file is first turned into a *tree*, the same
plain data a project's YAML files hold: dicts with string keys, lists,
strings, numbers, booleans and None, plus ``bytes`` (an imported file) and
:class:`Matrix` (rows of numbers, e.g. a polygon's points). A codec only turns
any tree into bytes and back. It knows nothing about projects, so a new field
in the model reaches every format without changing any codec; the documents
themselves are built in :mod:`mems_sketch.storage.document`.

Each codec reads back exactly the tree it wrote, with one exception: a
:class:`Matrix` comes back as a list of rows from YAML and JSON (they have no
matrix type), so code reading a tree accepts both (:func:`rows`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class MissingDependency(ValueError):
    """A format needs a package that is not installed (e.g. scipy for .mat)."""


@dataclass(frozen=True)
class Matrix:
    """Rows of numbers: a ``double`` matrix in MATLAB, a list of rows elsewhere."""

    rows: tuple[tuple[float, ...], ...]

    @classmethod
    def of(cls, rows: Sequence[Sequence[float]]) -> Matrix:
        return cls(tuple(tuple(float(v) for v in row) for row in rows))

    def tolist(self) -> list[list[float]]:
        return [list(row) for row in self.rows]


def rows(value: Any) -> list[list[float]]:
    """The rows of a :class:`Matrix`, or of a list of rows (as YAML and JSON give it)."""
    if isinstance(value, Matrix):
        return value.tolist()
    return [[float(v) for v in row] for row in value]


@dataclass(frozen=True)
class Codec:
    name: str  # "json"
    description: str  # "JSON"
    suffixes: tuple[str, ...]  # the first one is used for new files
    dump: Callable[[Any], bytes]
    load: Callable[[bytes], Any]


def codecs() -> dict[str, Codec]:
    """Every codec by name."""
    from mems_sketch.storage.formats import json_format, mat_format, xml_format, yaml_codec

    return {
        c.name: c for c in (yaml_codec.CODEC, json_format.CODEC, xml_format.CODEC, mat_format.CODEC)
    }


def codec_for(path: str | Path) -> Codec | None:
    """The codec for a file, by its suffix (None if there is none)."""
    suffix = Path(path).suffix.lower()
    return next((c for c in codecs().values() if suffix in c.suffixes), None)


def read(path: str | Path) -> Any:
    """The tree in a file."""
    codec = codec_for(path)
    if codec is None:
        raise ValueError(f"no format for '{Path(path).suffix}' files")
    try:
        return codec.load(Path(path).read_bytes())
    except (OSError, MissingDependency):
        raise
    except Exception as exc:  # each parser has its own errors: one message for all
        raise ValueError(
            f"{Path(path).name} is not a readable {codec.description} file: {exc}"
        ) from exc


def write(path: str | Path, tree: Any) -> Path:
    """Write a tree to a file in the format its suffix names."""
    codec = codec_for(path)
    if codec is None:
        raise ValueError(f"no format for '{Path(path).suffix}' files")
    path = Path(path)
    path.write_bytes(codec.dump(tree))
    return path
