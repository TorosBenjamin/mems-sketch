"""Parametric MEMS layout design.

Scripting entry point::

    from mems_sketch import Project, Layer, Instance, load, save, export

A project is a folder of YAML files (the source of truth); the compiler turns
it into geometry, which can be checked, etch-processed and exported.
"""

from mems_sketch.core.compiler import Compiler
from mems_sketch.core.component import Component, Geometry, Params, register_component
from mems_sketch.core.process import Layer, Process
from mems_sketch.core.project import Instance, Library, Project
from mems_sketch.core.shapes import (
    Align,
    ArcShape,
    ArrayModifier,
    BooleanShape,
    CircleShape,
    FilletShape,
    GroupShape,
    GuideShape,
    LayerMapShape,
    MirrorModifier,
    OffsetShape,
    PathShape,
    PolarArrayModifier,
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
    "ArrayModifier",
    "BooleanShape",
    "CircleShape",
    "Compiler",
    "Component",
    "ComponentDef",
    "FilletShape",
    "Geometry",
    "GroupShape",
    "GuideShape",
    "Instance",
    "Layer",
    "LayerMapShape",
    "Library",
    "MirrorModifier",
    "OffsetShape",
    "ParamDef",
    "Params",
    "PathShape",
    "PointDef",
    "PolarArrayModifier",
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
