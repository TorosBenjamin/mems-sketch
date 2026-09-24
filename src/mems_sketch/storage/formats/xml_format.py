"""XML, for any tree, readable without a schema::

    <mems-sketch>
      <format>mems-sketch/1</format>
      <top type="null"/>
      <components>
        <top>
          <parameters type="list">
            <item><name>pitch</name><default type="int">20</default></item>
          </parameters>
        </top>
        <entry key="comb/finger">...</entry>
      </components>
    </mems-sketch>

* A dict's entries are child elements named by their keys; a key that is not
  an XML name (``comb/finger``, ``5/0``) is written as ``<entry key="...">``.
* A list's elements are ``<item>`` children of an element with ``type="list"``.
* Text is a string; other values say their type: ``int``, ``float``, ``bool``,
  ``null``, ``base64`` (bytes), ``matrix`` (rows ``x y; x y``) and ``map``
  (only needed for an empty dict).
"""

from __future__ import annotations

import base64
import re
import xml.etree.ElementTree as ET
from typing import Any

from mems_sketch.storage.formats import Codec, Matrix

ROOT = "mems-sketch"
ENTRY, ITEM = "entry", "item"
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


def _tag(key: str) -> bool:
    """Whether ``key`` can be an element name as it is."""
    return bool(_NAME.match(key)) and not key.lower().startswith("xml") and key != ENTRY


def _number(value: float) -> str:
    return str(int(value)) if value.is_integer() and abs(value) < 1e15 else repr(value)


def _fill(element: ET.Element, tree: Any) -> None:
    if isinstance(tree, dict):
        if not tree:
            element.set("type", "map")
        for key, value in tree.items():
            if _tag(key):
                child = ET.SubElement(element, key)
            else:
                child = ET.SubElement(element, ENTRY, key=key)
            _fill(child, value)
    elif isinstance(tree, list):
        element.set("type", "list")
        for value in tree:
            _fill(ET.SubElement(element, ITEM), value)
    elif isinstance(tree, Matrix):
        element.set("type", "matrix")
        element.text = "; ".join(" ".join(_number(v) for v in row) for row in tree.rows)
    elif isinstance(tree, bytes):
        element.set("type", "base64")
        element.text = base64.b64encode(tree).decode("ascii")
    elif tree is None:
        element.set("type", "null")
    elif isinstance(tree, bool):
        element.set("type", "bool")
        element.text = "true" if tree else "false"
    elif isinstance(tree, int):
        element.set("type", "int")
        element.text = str(tree)
    elif isinstance(tree, float):
        element.set("type", "float")
        element.text = repr(tree)
    elif isinstance(tree, str):
        element.text = tree
    else:
        raise TypeError(f"cannot write {type(tree).__name__} to XML")


def _read(element: ET.Element) -> Any:
    kind = element.get("type")
    text = element.text or ""
    if kind == "list":
        return [_read(child) for child in element]
    if kind == "matrix":
        return Matrix.of(
            [[float(v) for v in row.split()] for row in text.split(";") if row.strip()]
        )
    if kind == "base64":
        return base64.b64decode(text.strip())
    if kind == "null":
        return None
    if kind == "bool":
        return text.strip() == "true"
    if kind == "int":
        return int(text.strip())
    if kind == "float":
        return float(text.strip())
    if kind == "map" or len(element):
        return {
            (child.get("key", "") if child.tag == ENTRY else child.tag): _read(child)
            for child in element
        }
    if kind not in (None, "str"):
        raise ValueError(f"unknown type '{kind}' in <{element.tag}>")
    return text


def dump(tree: Any) -> bytes:
    root = ET.Element(ROOT)
    _fill(root, tree)
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def load(data: bytes) -> Any:
    root = ET.fromstring(data)
    if root.tag != ROOT:
        raise ValueError(f"expected <{ROOT}>, found <{root.tag}>")
    return _read(root)


CODEC = Codec("xml", "XML", (".xml",), dump, load)
