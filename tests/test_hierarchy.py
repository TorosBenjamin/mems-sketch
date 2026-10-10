"""Placed components stay placed: a component placed many times is built once,
held once and drawn once (geometry with instances)."""

import pytest

from mems_sketch import ArrayModifier, ComponentDef, Engine, ParamDef, RectShape
from mems_sketch.core.component import Geometry
from mems_sketch.core.project import new_project
from mems_sketch.core.shapes import RefShape
from mems_sketch.core.transform import Transform


def project():
    p = new_project()
    p.define_component(
        ComponentDef(
            name="pad",
            parameters=[ParamDef(name="w", default=4)],
            shapes=[RectShape(layer="device", x0=0, y0=0, x1="w", y1=2)],
        )
    )
    p.define_component(
        ComponentDef(
            name="row",
            parameters=[ParamDef(name="gap", default=10)],
            shapes=[
                RefShape(
                    name="pads",
                    component="pad",
                    modifiers=[ArrayModifier(columns=3, rows=1, dx="gap", dy=0)],
                ),
                RectShape(name="bar", layer="metal", x0=0, y0=-5, x1=24, y1=-3),
            ],
        )
    )
    return p


def test_a_component_placed_three_times_is_one_geometry_placed_three_times():
    geometry = Engine().load(project()).build("row").geometry
    assert list(geometry.own) == ["metal"]
    assert len(geometry.instances) == 3
    pads = {id(g) for g, _ in geometry.instances}
    assert len(pads) == 1
    assert [t.dx for _, t in geometry.instances] == [0, 10, 20]
    assert geometry.bbox() == (0, -5, 24, 2)
    assert sorted(geometry.layers) == ["device", "metal"]  # flattened when asked for
    assert geometry.layers["device"].area() == 3 * 4 * 2 * 1_000_000


def test_an_edit_elsewhere_keeps_the_placed_geometry():
    engine = Engine().load(project())
    before = engine.build("row").geometry
    changed = project()
    changed.components["row"].parameters[0].default = 12.0
    after = engine.trial(changed).build("row").geometry
    assert after is not before
    assert after.instances[0][0] is before.instances[0][0]  # the pad was not built again
    assert [t.dx for _, t in after.instances] == [0, 12, 24]


def test_touches_and_boxes_look_through_placements():
    pad = Geometry()
    pad.add_rect("device", 0, 0, 4, 2)
    top = Geometry()
    top.place(pad, Transform(100, 0, 90))  # turned a quarter: now 2 wide and 4 high
    top.place(pad, Transform(0, 50, 0, True))  # mirrored about x: y from -2 to 0
    assert top.bbox() == pytest.approx((0, 0, 100, 50))
    assert top.touches(99, 3)
    assert not top.touches(101, 3)
    assert top.touches(2, 49)
    assert not top.touches(2, 51)
    leaves = top.leaves()
    assert [g for g, _ in leaves] == [pad, pad]
    assert top.only(["metal"]).is_empty()
    assert top.layers["device"].area() == 2 * 8 * 1_000_000


def test_merge_keeps_what_is_placed_placed():
    pad = Geometry()
    pad.add_rect("device", 0, 0, 1, 1)
    group = Geometry()
    group.place(pad, Transform(5, 0))
    top = Geometry()
    top.merge(group, Transform(0, 10))
    [(placed, at)] = top.instances
    assert placed is pad and (at.dx, at.dy) == (5, 10)
