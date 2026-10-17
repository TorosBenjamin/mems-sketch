"""An edit rebuilds only the shapes it reaches (QS-7): the others come from the cache."""

import pytest

from mems_sketch import Align, BooleanShape, CircleShape, RectShape
from mems_sketch.editing import EditSession


def bar(name: str, k: int, **fields) -> RectShape:
    return RectShape(
        name=name, layer="device", **({"x0": 10 * k, "y0": 0, "x1": 10 * k + 4, "y1": 2} | fields)
    )


@pytest.fixture
def doc() -> EditSession:
    session = EditSession()
    session.parameters.set("w", 4.0)
    for k in range(20):
        session.nodes.add(bar(f"b{k}", k))
    session.nodes.add(bar("wide", 30, x1="300 + w"))
    session.results.geometry()
    session.results.inspection()
    return session


def built(doc, edit) -> int:
    """How many shapes ``edit`` made the engine evaluate again (geometry and records)."""
    before = doc.engine.cached_nodes
    edit()
    doc.results.geometry()
    doc.results.inspection()
    return doc.engine.cached_nodes - before


def box(doc, path):
    return doc.results.inspection()[path].geometry.bbox()


def test_changing_one_shape_rebuilds_that_shape_only(doc):
    assert built(doc, lambda: doc.nodes.replace(((0, 3),), bar("b3", 3, y1=5))) == 1
    assert box(doc, ((0, 3),))[3] == pytest.approx(5)


def test_a_parameter_rebuilds_the_shapes_that_use_it(doc):
    assert built(doc, lambda: doc.parameters.update("w", default=6.0)) == 1
    assert box(doc, ((0, 20),))[2] == pytest.approx(306)


def test_shapes_that_use_a_changed_shapes_points_follow_it(doc):
    doc.nodes.add(bar("tag", 0, align=Align(to="b0.top_right", point="bottom_left")))
    doc.nodes.add(bar("tip", 0, x0="tag.right.x", x1="tag.right.x + 1"))
    assert box(doc, ((0, 22),))[0] == pytest.approx(8)
    assert built(doc, lambda: doc.nodes.replace(((0, 0),), bar("b0", 0, x1=10))) == 3
    assert box(doc, ((0, 21),))[:2] == pytest.approx((10, 2))
    assert box(doc, ((0, 22),))[0] == pytest.approx(14)


def test_a_change_inside_an_operation_rebuilds_it_and_the_changed_part(doc):
    plate = BooleanShape(
        name="plate",
        op="subtract",
        a=[RectShape(name="body", layer="device", x0=0, y0=10, x1=40, y1=30)],
        b=[CircleShape(name="hole", layer="device", x=10, y=20, radius=3)],
    )
    doc.nodes.add(plate)
    doc.results.inspection()
    moved = plate.model_copy(deep=True)
    moved.b[0].x = 20
    assert built(doc, lambda: doc.nodes.replace(((0, 21),), moved)) == 2  # the plate and the hole


def test_a_change_to_a_placed_component_reaches_its_placements(doc):
    top = doc.active
    doc.components.new("cell")
    doc.nodes.add(RectShape(name="pad", layer="device", x0=0, y0=0, x1=5, y1=5))
    doc.set_active(top)
    path = doc.nodes.add_component("cell", x=0, y=50)
    assert box(doc, path)[2] == pytest.approx(5)
    doc.set_active("cell")
    doc.nodes.replace(((0, 0),), RectShape(name="pad", layer="device", x0=0, y0=0, x1=9, y1=5))
    doc.set_active(top)
    assert box(doc, path)[2] == pytest.approx(9)


def test_a_process_constant_reaches_the_shapes_that_use_it(doc):
    doc.process.set_constant("gap", 1.0)
    doc.nodes.add(bar("spaced", 0, y0=10, y1="10 + process.gap"))
    doc.results.inspection()
    assert built(doc, lambda: doc.process.set_constant("gap", 3.0)) == 1
    assert box(doc, ((0, 21),))[3] == pytest.approx(13)
