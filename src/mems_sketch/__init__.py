"""Parametric MEMS layout design.

Scripting entry point::

    from mems_sketch import Project, Layer, Instance, load, save, export

A project is a folder of YAML files (the source of truth); the engine turns
it into geometry, which can be checked and exported.
"""

from importlib.metadata import PackageNotFoundError, version

from mems_sketch.core.component import Component, Geometry, Params
from mems_sketch.core.process import Layer, Process
from mems_sketch.core.project import Instance, Library, Project
from mems_sketch.core.shapes import (
    Align,
    ArcShape,
    ArrayModifier,
    BooleanShape,
    CircleShape,
    Corner,
    CornersModifier,
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
from mems_sketch.engine import Build, Engine
from mems_sketch.export.base import export, register_exporter
from mems_sketch.storage import load, load_library, save

try:
    __version__ = version("mems-sketch")
except PackageNotFoundError:  # run from a source tree that is not installed
    __version__ = "0.0.0"

__all__ = [
    "Align",
    "ArcShape",
    "ArrayModifier",
    "BooleanShape",
    "Build",
    "CircleShape",
    "Component",
    "ComponentDef",
    "Corner",
    "CornersModifier",
    "Engine",
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
    "register_exporter",
    "save",
]
