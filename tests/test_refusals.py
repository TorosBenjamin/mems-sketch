"""Values that would draw nothing, or nonsense, are refused with the reason
instead of being accepted without a word (#253)."""

import pytest

from mems_sketch import ArrayModifier, CircleShape, PolarArrayModifier, RectShape
from mems_sketch.core.errors import explain
from mems_sketch.core.shapes.modifiers import PerforateModifier
from mems_sketch.editing import EditSession


@pytest.fixture
def doc() -> EditSession:
    session = EditSession()
    session.nodes.add(RectShape(name="a", layer="device", x0=0, y0=0, x1=40, y1=40))
    return session


def refused(action) -> str:
    with pytest.raises(ValueError) as caught:
        action()
    return explain(caught.value)


def square(**fields) -> RectShape:
    return RectShape(name="a", layer="device", x0=0, y0=0, x1=40, y1=40, **fields)


def test_a_circle_needs_a_radius(doc):
    for radius in (0, -3):
        circle = CircleShape(name="c", layer="device", radius=radius)
        assert "circle radius must be more than 0" in refused(lambda c=circle: doc.nodes.add(c))
    assert [s.name for s in doc.shapes] == ["a"]


@pytest.mark.parametrize(
    ("modifier", "why"),
    [
        (ArrayModifier(columns=0, dx=10), "array columns must be a whole number, at least 1"),
        (PolarArrayModifier(count=0), "polar array count must be a whole number, at least 1"),
        (PolarArrayModifier(count=4, step=0), "polar array step must not be 0"),
        (PerforateModifier(hole_size=20, pitch=3), "must be smaller than their pitch"),
    ],
)
def test_modifiers_that_would_draw_nothing_or_nonsense(doc, modifier, why):
    assert why in refused(lambda: doc.nodes.replace(((0, 0),), square(modifiers=[modifier])))
    assert doc.shapes[0].modifiers == []


def test_a_port_is_on_a_layer_of_the_project(doc):
    assert "there is no layer 'nolayer'" in refused(
        lambda: doc.ports.add(at="a.right", name="out", layer="nolayer")
    )
    doc.ports.add(at="a.right", name="out", layer="level")  # relative: fine
    assert "there is no layer 'gold'" in refused(lambda: doc.ports.update("out", layer="gold"))


def test_a_route_bend_radius_is_not_negative():
    from mems_sketch.core.shapes import RouteShape

    doc = EditSession()
    doc.nodes.add_component("std.bond_pad", x=0)
    doc.nodes.add_component("std.bond_pad", x=400)
    names = [s.name for s in doc.shapes]
    route = RouteShape(
        name="w",
        start=f"{names[0]}.port.signal",
        end=f"{names[1]}.port.signal",
        bend="round",
        radius=-3,
    )
    assert "bend radius must not be negative" in refused(lambda: doc.nodes.add(route))
