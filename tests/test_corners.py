"""The corners modifier: chosen corners rounded or chamfered, each its own radius."""

import math

import pytest

from mems_sketch import (
    BooleanShape,
    ComponentDef,
    Corner,
    CornersModifier,
    Layer,
    ParamDef,
    PolygonShape,
    Project,
    RectShape,
    RefShape,
    load,
    save,
)
from mems_sketch.core.component import to_dbu
from mems_sketch.core.shapes import Align, ArrayModifier
from mems_sketch.editing import EditSession

CUT = 1 - math.pi / 4  # what rounding a right angle with radius 1 removes (or adds)


def area(design: Project, component: str | None = None) -> float:
    region = design.render(component).layers.get("device")
    return 0.0 if region is None else region.area() / to_dbu(1) ** 2


def corners(*items: Corner) -> list[CornersModifier]:
    return [CornersModifier(corners=list(items))]


@pytest.fixture
def design() -> Project:
    d = Project()
    d.add_layer(Layer("device", 1))
    return d


def bar(**kw) -> RectShape:
    return RectShape(name="bar", layer="device", x0=0, y0=0, x1=10, y1=4, **kw)


def test_one_corner_rounded_the_others_sharp(design):
    design.add(bar(modifiers=corners(Corner(at="self.top_right", radius=1))))
    assert area(design) == pytest.approx(40 - CUT, abs=0.01)
    box = design.render().layers["device"].bbox()
    assert (box.left, box.bottom, box.right, box.top) == (0, 0, 10000, 4000)


def test_each_corner_has_its_own_radius_and_style(design):
    design.add(
        bar(
            modifiers=corners(
                Corner(at="self.top_right", radius=2),
                Corner(at="self.bottom_left", radius=1, style="chamfer"),
            )
        )
    )
    assert area(design) == pytest.approx(40 - 4 * CUT - 0.5, abs=0.02)


def test_a_concave_corner_gains_material(design):
    ell = [(0, 0), (10, 0), (10, 2), (2, 2), (2, 8), (0, 8)]
    design.add(PolygonShape(layer="device", points=ell, modifiers=corners(Corner(x=2, y=2))))
    assert area(design) == pytest.approx(10 * 2 + 2 * 6 + CUT, abs=0.01)


def test_a_corner_made_by_a_cut_follows_the_parameters(design):
    design.components[design.top].parameters.append(ParamDef(name="slot_x", default=45))
    beam = RectShape(name="beam", layer="device", x0=0, y0=0, x1=100, y1=20)
    slot = RectShape(name="slot", layer="device", x0="slot_x", y0=10, x1="slot_x + 10", y1=25)
    corner = Corner(x="slot_x", y=20, radius=2)  # the slot's own left edge
    design.add(
        BooleanShape(name="cut", op="subtract", a=[beam], b=[slot], modifiers=corners(corner))
    )
    assert area(design) == pytest.approx(2000 - 100 - 4 * CUT, abs=0.05)
    design.components[design.top].parameters[0] = ParamDef(name="slot_x", default=20)
    assert area(design) == pytest.approx(2000 - 100 - 4 * CUT, abs=0.05)


def test_a_corner_that_is_not_there_is_an_error(design):
    with pytest.raises(ValueError, match=r"\(5, 4\) is not a corner"):
        design.add(bar(modifiers=corners(Corner(x=5, y=4))))


def test_a_radius_too_large_is_an_error(design):
    with pytest.raises(ValueError, match="does not fit"):
        design.add(bar(modifiers=corners(Corner(at="self.top_right", radius=5))))


def test_rounding_a_placed_part_leaves_the_part_alone(design):
    design.components["pad"] = ComponentDef(name="pad", shapes=[bar()])
    corner = Corner(at="self.bottom_left", radius=1)
    design.add(RefShape(component="pad", x=100, modifiers=corners(corner)))
    assert area(design) == pytest.approx(40 - CUT, abs=0.01)
    assert area(design, "pad") == pytest.approx(40)


def test_saved_and_loaded(design, tmp_path):
    design.add(bar(modifiers=corners(Corner(at="self.top_right", radius="1 + 1"))))
    save(design, tmp_path / "p")
    again = load(tmp_path / "p")
    assert area(again) == pytest.approx(area(design))


# -- picking corners (editing) --------------------------------------------------


def session_with(*shapes, parameters=()) -> EditSession:
    project = Project()
    project.add_layer(Layer("device", 1))
    project.components[project.top].parameters.extend(parameters)
    for shape in shapes:
        project.add(shape)
    return EditSession(project)


def device_area(session: EditSession) -> float:
    region = session.results.geometry().layers["device"]
    return region.area() / to_dbu(1) ** 2


def test_the_corners_that_can_be_picked():
    session = session_with(bar())
    assert sorted(session.corners.candidates(((0, 0),))) == [(0, 0), (0, 4), (10, 0), (10, 4)]


def test_a_picked_corner_is_recorded_as_the_shapes_own_point():
    session = session_with(bar())
    path = ((0, 0),)
    assert session.corners.add(path, 10, 4, radius=1) == 0
    assert session.node(path).modifiers[0].corners[0].at == "self.top_right"
    assert device_area(session) == pytest.approx(40 - CUT, abs=0.01)
    assert session.corners.rounded(path) == [(0, (10, 4))]
    assert session.corners.add(path, 10, 4) == 0  # not twice
    assert len(session.node(path).modifiers[0].corners) == 1
    session.corners.update(path, 0, radius=2, style="chamfer")
    assert device_area(session) == pytest.approx(40 - 2, abs=0.01)
    session.corners.remove(path, 0)
    assert session.node(path).modifiers == []


def test_a_corner_a_cut_made_is_recorded_with_the_cuts_expressions():
    beam = RectShape(name="beam", layer="device", x0=0, y0=0, x1=100, y1=20)
    slot = RectShape(name="slot", layer="device", x0="slot_x", y0=10, x1="slot_x + 10", y1=25)
    session = session_with(
        BooleanShape(name="cut", op="subtract", a=[beam], b=[slot]),
        parameters=[ParamDef(name="slot_x", default=45)],
    )
    path = ((0, 0),)
    session.corners.add(path, 45, 20, radius=2)
    corner = session.node(path).modifiers[0].corners[0]
    assert (corner.x, corner.y) == ("slot_x", "self.top.y")
    session.parameters.update("slot_x", default=20)
    assert session.corners.rounded(path) == [(0, (20, 20))]


def test_a_rounded_part_is_copied_rounded():
    arrayed = bar(modifiers=[ArrayModifier(columns=3, dx=20)])
    session = session_with(arrayed)
    path = ((0, 0),)
    assert len(session.corners.candidates(path)) == 4  # of the original, before the array
    session.corners.add(path, 0, 0, radius=1)
    assert isinstance(session.node(path).modifiers[0], CornersModifier)
    assert device_area(session) == pytest.approx(3 * (40 - CUT), abs=0.03)


def test_corners_of_an_aligned_shape_are_where_it_is_shown():
    anchor = RectShape(name="anchor", layer="device", x0=50, y0=50, x1=60, y1=60)
    moved = bar(align=Align(point="bottom_left", to="anchor.top_right"))
    session = session_with(anchor, moved)
    path = ((0, 1),)
    assert (60, 60) in session.corners.candidates(path)
    session.corners.add(path, 70, 64, radius=1)
    assert session.node(path).modifiers[0].corners[0].at == "self.top_right"
    assert session.corners.rounded(path) == [(0, (70, 64))]
