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
