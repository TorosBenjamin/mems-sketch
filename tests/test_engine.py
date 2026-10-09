"""The engine interface: what the GUI, editing, the command line and the exporters
build through (core-architecture.md, migration step 1)."""

import pytest

from mems_sketch import ComponentDef, Engine, ParamDef, RectShape
from mems_sketch.core.project import new_project
from mems_sketch.core.user_component import PointDef
from mems_sketch.engine import build_shapes


def plate_project():
    project = new_project()
    project.define_component(
        ComponentDef(
            name="plate",
            parameters=[ParamDef(name="w", default=10)],
            shapes=[RectShape(name="body", layer="device", x0=0, y0=0, x1="w", y1=5)],
            points=[PointDef(name="tip", x="w", y=0)],
        )
    )
    return project


def test_a_build_is_the_component_with_its_values():
    engine = Engine().load(plate_project())
    build = engine.build("plate", {"w": 20})
    assert build.layers() == ["device"]
    assert build.bbox() == (0, 0, 20, 5)
    [outline] = build.outlines("device")
    assert sorted(outline.hull) == [(0, 0), (0, 5), (20, 0), (20, 5)] and outline.holes == []
    assert build.points() == {"tip": (20, 0)}
    assert build.variables()["w"] == 20
    assert engine.build("plate").bbox() == (0, 0, 10, 5)  # the default


def test_records_place_every_node():
    build = Engine().load(plate_project()).build("plate")
    [(path, record)] = build.records().items()
    assert path == ((0, 0),) and record.geometry.bbox() == (0, 0, 10, 5)
    assert record.inner.is_identity


def test_loading_an_edited_project_rebuilds_it():
    project = plate_project()
    engine = Engine().load(project)
    assert engine.build("plate").bbox() == (0, 0, 10, 5)
    project.components["plate"].parameters[0] = ParamDef(name="w", default=30)
    assert engine.load(project).build("plate").bbox() == (0, 0, 30, 5)


def test_a_trial_leaves_the_loaded_project_alone():
    project = plate_project()
    engine = Engine().load(project)
    variant = plate_project()
    variant.components["plate"].shapes[0] = RectShape(
        name="body", layer="device", x0=0, y0=0, x1=1, y1=1
    )
    assert engine.trial(variant).build("plate").bbox() == (0, 0, 1, 1)
    assert engine.build("plate").bbox() == (0, 0, 10, 5)


def test_problems_and_errors():
    project = plate_project()
    project.components["plate"].shapes[0] = RectShape(
        name="body", layer="device", x0=0, y0=0, x1="nowhere", y1=5
    )
    engine = Engine().load(project)
    [problem] = engine.problems()
    assert problem.startswith("plate:") and "nowhere" in problem
    with pytest.raises(Exception, match="nowhere"):
        engine.build("plate").geometry  # noqa: B018
    with pytest.raises(RuntimeError, match="load"):
        Engine().build("plate")


def test_shapes_on_their_own():
    geometry = build_shapes([RectShape(layer="device", x0=0, y0=0, x1=2, y1=3)])
    assert geometry.bbox() == (0, 0, 2, 3)
