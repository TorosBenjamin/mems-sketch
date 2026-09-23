"""Every registered shape kind works with the rest of the system.

A new kind in core/shapes/kinds is checked here without adding tests: it must
survive a YAML round trip, render, move exactly, and describe itself.
"""

import klayout.db as kdb
import pytest
import yaml

from mems_sketch.core.component import get_component
from mems_sketch.core.shapes import (
    KINDS,
    SHAPE_ADAPTER,
    Evaluator,
    RectShape,
    RefShape,
    TransformShape,
    default_shape,
    kind_class,
    translated,
    wrap_shapes,
)

LAYER = "device"


def two_rects():
    return [
        RectShape(name="r1", layer=LAYER, x0=0, y0=0, x1=100, y1=50),
        RectShape(name="r2", layer=LAYER, x0=50, y0=10, x1=150, y1=60),
    ]


def samples():
    """One node of every kind: the primitives' defaults, every wrap, a reference."""
    shapes = [kind.default(LAYER) for kind in KINDS if kind.category == "primitive"]
    shapes += [wrap_shapes(op, f"w_{op}", two_rects()) for kind in KINDS for op in kind.wraps]
    shapes.append(RefShape(component="rectangle", x=5))
    return shapes


SAMPLES = samples()


def label(shape):
    return shape.name or shape.kind


def bbox(shape):
    box = kdb.Box()
    for region in Evaluator(get_component).render([shape], {}).layers.values():
        box += region.bbox()
    return box


def test_every_kind_has_a_sample():
    assert {type(s) for s in SAMPLES} == set(KINDS)


@pytest.mark.parametrize("kind", KINDS, ids=lambda k: k.kind_name())
def test_every_kind_declares_what_it_is(kind):
    assert kind.category in ("primitive", "operation", "reference")
    assert kind_class(kind.kind_name()) is kind
    assert kind.icon


@pytest.mark.parametrize("shape", SAMPLES, ids=label)
def test_a_yaml_round_trip_keeps_the_shape(shape):
    text = yaml.safe_dump(SHAPE_ADAPTER.dump_python(shape, mode="json"))
    assert SHAPE_ADAPTER.validate_python(yaml.safe_load(text)) == shape


@pytest.mark.parametrize("shape", SAMPLES, ids=label)
def test_the_shape_renders(shape):
    assert not bbox(shape).empty()


@pytest.mark.parametrize("shape", SAMPLES, ids=label)
def test_moving_shifts_the_geometry_exactly(shape):
    before, after = bbox(shape), bbox(translated(shape, 3, -2))
    assert (after.left - before.left, after.bottom - before.bottom) == (3000, -2000)
    assert (after.width(), after.height()) == (before.width(), before.height())


@pytest.mark.parametrize("shape", SAMPLES, ids=label)
def test_the_shape_describes_itself(shape):
    assert shape.summary()
    assert shape.icon_name()


def test_unknown_names_are_rejected():
    with pytest.raises(ValueError, match="unknown primitive"):
        default_shape("ref")
    with pytest.raises(ValueError, match="unknown operation"):
        wrap_shapes("explode", "x", two_rects())
    with pytest.raises(KeyError, match="unknown shape kind"):
        kind_class("nope")


def test_the_old_group_kind_is_read_as_a_transform():
    shape = SHAPE_ADAPTER.validate_python({"kind": "group", "children": []})
    assert isinstance(shape, TransformShape) and shape.kind == "transform"
    assert kind_class("group") is TransformShape
