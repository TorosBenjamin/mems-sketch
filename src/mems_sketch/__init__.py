"""Parametric MEMS layout design.

Scripting entry point::

    from mems_sketch import Project, Layer, Instance, load, save, export

A project is a folder of YAML files (the source of truth); the compiler turns
it into geometry, which can be checked, etch-processed and exported.
"""

from mems_sketch.core.component import Component, Geometry, Params, register_component
from mems_sketch.core.compiler import Compiler
from mems_sketch.core.process import Layer, Process
from mems_sketch.core.project import Instance, Library, Project
from mems_sketch.core.shapes import (
    Align,
    ArcShape,
    BooleanShape,
    CircleShape,
    FilletShape,
    GroupShape,
    LayerMapShape,
    OffsetShape,
    PathShape,
    PolygonShape,
    RectShape,
    RefShape,
    Repeat,
    Shape,
    TransformShape,
)
from mems_sketch.core.user_component import ComponentDef, ParamDef, PointDef
from mems_sketch.export.base import export, register_exporter
from mems_sketch.storage import load, load_library, save

__all__ = [
    "Align",
    "ArcShape",
    "BooleanShape",
    "CircleShape",
    "Component",
    "ComponentDef",
    "Compiler",
    "FilletShape",
    "Geometry",
    "GroupShape",
    "Instance",
    "Layer",
    "LayerMapShape",
    "Library",
    "OffsetShape",
    "ParamDef",
    "Params",
    "PointDef",
    "PathShape",
    "PolygonShape",
    "Process",
    "Project",
    "RectShape",
    "RefShape",
    "Repeat",
    "Shape",
    "TransformShape",
    "export",
    "load",
    "load_library",
    "register_component",
    "register_exporter",
    "save",
]
