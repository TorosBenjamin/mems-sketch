"""Declared points: measured from the component's own box, renamed with their users."""

import pytest

from mems_sketch.core.project import Project
from mems_sketch.core.shapes import Align, RectShape, RefShape
from mems_sketch.core.user_component import ComponentDef, PointDef
from mems_sketch.editing import EditSession


@pytest.fixture
def session() -> EditSession:
    """``top`` places the spring twice and uses its point ``tip`` every way it can."""
    spring = ComponentDef(
        name="spring",
        shapes=[RectShape(name="beam", layer="device", x0=0, y0=0, x1=40, y1=4)],
        points=[PointDef(name="tip", at="beam.right")],
    )
    top = ComponentDef(
        name="top",
        shapes=[
            RefShape(name="s1", component="spring"),
            RefShape(
                name="s2",
                component="spring",
                align=Align(point="tip", to="s1.tip", dx=10),
            ),
            RectShape(name="pad", layer="device", x0="s1.tip.x", y0=0, x1="s1.tip.x + 5", y1=5),
        ],
        points=[PointDef(name="out", at="s2.tip")],
    )
    return EditSession(Project(components={"spring": spring, "top": top}, top="top"))


def test_a_point_can_be_measured_from_the_whole_component(session):
    session.set_active("spring")
    session.points.update("tip", at="top_right", y=-1)
    assert session.results.declared_points()["tip"] == (40, 3)
    assert session.results.default_points()["center"] == (20, 2)


def test_renaming_a_point_updates_the_components_placing_it(session):
    before = session.results.declared_points("top")
    session.set_active("spring")
    session.points.update("tip", name="end")
    top = session.project.components["top"]
    _, s2, pad = top.shapes
    assert (s2.align.point, s2.align.to) == ("end", "s1.end")
    assert (pad.x0, pad.x1) == ("s1.end.x", "s1.end.x + 5")
    assert top.points[0].at == "s2.end"
    assert session.results.declared_points("top") == before  # nothing moved

    session.undo()
    assert session.project.components["top"].shapes[1].align.to == "s1.tip"


def test_a_point_name_is_checked(session):
    session.set_active("spring")
    session.points.add()
    with pytest.raises(ValueError, match="already exists"):
        session.points.update("point1", name="tip")
    with pytest.raises(ValueError, match="bounding-box point"):
        session.points.update("point1", name="center")


def test_naming_a_shape_point(session):
    session.set_active("spring")
    name = session.points.add(at="beam.top_left")
    assert name == "beam_top_left"
    assert session.results.declared_points()[name] == (0, 4)


def test_the_points_of_named_shapes(session):
    session.set_active("top")
    points = session.results.shape_points()
    assert set(points) == {"s1", "s2", "pad"}
    assert points["s1"]["tip"] == (40, 2)  # a placed component's declared points too


def test_re_exporting_points_of_placed_parts_keeps_their_names(session):
    session.set_active("top")
    assert session.points.export(["s1.tip", "s2.tip", "s1.center"]) == [
        "tip",
        "s2_tip",
        "s1_center",
    ]
    points = session.results.declared_points()
    assert points["tip"] == (40, 2) and points["s2_tip"] == points["out"]
    session.undo()  # one step
    assert [p.name for p in session.active_definition.points] == ["out"]
