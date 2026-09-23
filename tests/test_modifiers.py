"""Modifier stacks: array, polar array and mirror, their order, editing and Apply."""

import klayout.db as kdb
import pytest

from mems_sketch import (
    ArrayModifier,
    MirrorModifier,
    PolarArrayModifier,
    RectShape,
    Repeat,
    TransformShape,
)
from mems_sketch.core.shapes import SHAPE_ADAPTER, CircleShape
from mems_sketch.editing import EditSession
from mems_sketch.storage import yaml_format


def rect(name="bar", x0=10.0, y0=0.0, x1=12.0, y1=4.0, **fields):
    return RectShape(name=name, layer="device", x0=x0, y0=y0, x1=x1, y1=y1, **fields)


@pytest.fixture
def doc() -> EditSession:
    return EditSession()


def region(doc: EditSession) -> kdb.Region:
    return doc.results.geometry().layers.get("device", kdb.Region())


def pieces(doc: EditSession) -> list[tuple[float, float, float, float]]:
    """The device polygons' bounding boxes in µm, sorted."""
    return sorted(
        tuple(v / 1000 for v in (p.bbox().left, p.bbox().bottom, p.bbox().right, p.bbox().top))
        for p in region(doc).each_merged()
    )


# -- what each modifier makes ------------------------------------------------------


def test_array_places_copies_on_a_grid_with_indices(doc):
    doc.nodes.add(rect(x1="11 + i", modifiers=[ArrayModifier(columns=3, dx=10)]))
    assert pieces(doc) == [(10, 0, 11, 4), (20, 0, 22, 4), (30, 0, 33, 4)]


def test_mirror_across_a_vertical_or_horizontal_line_or_both(doc):
    path = doc.nodes.add(rect(modifiers=[MirrorModifier(axis="x", x=5)]))
    assert pieces(doc) == [(-2, 0, 0, 4), (10, 0, 12, 4)]
    doc.modifiers.update(path, 0, axis="y", y=-1)
    assert pieces(doc) == [(10, -6, 12, -2), (10, 0, 12, 4)]
    doc.modifiers.update(path, 0, axis="both", x=0, y=-1)
    assert len(pieces(doc)) == 4
    doc.modifiers.update(path, 0, axis="x", x=0, keep=False)
    assert pieces(doc) == [(-12, 0, -10, 4)]  # only the mirror image


def test_polar_array_rotates_copies_about_a_centre(doc):
    path = doc.nodes.add(rect(x0=10, y0=-1, x1=14, y1=1, modifiers=[PolarArrayModifier(count=4)]))
    assert pieces(doc) == [(-14, -1, -10, 1), (-1, -14, 1, -10), (-1, 10, 1, 14), (10, -1, 14, 1)]
    doc.modifiers.update(path, 0, rotate=False)  # copies keep their orientation
    assert pieces(doc) == [(-14, -1, -10, 1), (-2, -13, 2, -11), (-2, 11, 2, 13), (10, -1, 14, 1)]
    doc.modifiers.update(path, 0, rotate=True, count=3, step=90, x=10, y=0)
    assert pieces(doc) == [(6, -1, 14, 4)]  # 0°, 90°, 180° about the bar's own end: one piece


def test_the_order_of_the_stack_matters(doc):
    array, mirror = ArrayModifier(columns=2, dx=5), MirrorModifier(axis="x", x=0)
    path = doc.nodes.add(rect(modifiers=[array, mirror]))
    after = pieces(doc)
    assert after == [(-17, 0, -15, 4), (-12, 0, -10, 4), (10, 0, 12, 4), (15, 0, 17, 4)]
    doc.modifiers.move(path, 1, 0)  # mirror first, then the array copies both halves
    assert pieces(doc) == [(-12, 0, -10, 4), (-7, 0, -5, 4), (10, 0, 12, 4), (15, 0, 17, 4)]
    doc.modifiers.set_enabled(path, 0, False)
    assert pieces(doc) == [(10, 0, 12, 4), (15, 0, 17, 4)]


def test_modifiers_can_use_points_of_other_shapes(doc):
    doc.nodes.add(rect("mass", x0=-5, y0=-5, x1=5, y1=5))
    doc.nodes.add(
        rect(
            "comb",
            x0=20,
            y0=-1,
            x1=30,
            y1=1,
            modifiers=[MirrorModifier(axis="x", x="mass.center.x")],
        )
    )
    assert pieces(doc)[0] == (-30, -1, -20, 1)
    doc.nodes.replace(((0, 0),), rect("mass", x0=5, y0=-5, x1=15, y1=5))  # centre at 10
    assert pieces(doc)[0] == (-10, -1, 0, 1)


def test_alignment_moves_the_whole_result_and_points_come_from_it(doc):
    doc.nodes.add(rect("base", x0=0, y0=0, x1=100, y1=10))
    doc.nodes.add(
        rect(
            "posts",
            x0=0,
            y0=0,
            x1=2,
            y1=5,
            modifiers=[ArrayModifier(columns=3, dx=10)],
            align={"point": "bottom_left", "to": "base.top_left"},
        )
    )
    assert pieces(doc)[:1] == [(0, 0, 100, 15)]  # the posts sit on top of the base


# -- files and the earlier repeat field ------------------------------------------------


def test_repeat_is_read_as_the_first_array_modifier():
    old = SHAPE_ADAPTER.validate_python(
        {
            "kind": "rect",
            "layer": "device",
            "x0": 0,
            "y0": 0,
            "x1": 1,
            "y1": 1,
            "repeat": {"columns": 2, "dx": 5},
        }
    )
    assert old.modifiers == [ArrayModifier(columns=2, dx=5)] and old.repeat is old.modifiers[0]
    assert Repeat is ArrayModifier
    both = rect(modifiers=[MirrorModifier(), ArrayModifier(columns=2)])
    data = {**both.model_dump(), "repeat": {"columns": 7}}  # sets the array, keeps the mirror
    changed = SHAPE_ADAPTER.validate_python(data)
    assert [m.kind for m in changed.modifiers] == ["mirror", "array"]
    assert changed.repeat.columns == 7
    removed = SHAPE_ADAPTER.validate_python({**both.model_dump(), "repeat": None})
    assert [m.kind for m in removed.modifiers] == ["mirror"]


def test_modifiers_round_trip_through_yaml():
    shape = CircleShape(
        name="hole",
        layer="device",
        radius=2,
        modifiers=[
            ArrayModifier(columns="n", dx="pitch"),
            PolarArrayModifier(count=6, x="mass.center.x", rotate=False),
            MirrorModifier(axis="both", enabled=False),
        ],
    )
    data = yaml_format.to_data(shape)
    assert list(data)[-1] == "modifiers"
    assert data["modifiers"][0] == {"kind": "array", "columns": "n", "dx": "pitch"}
    assert SHAPE_ADAPTER.validate_python(data) == shape


# -- editing -----------------------------------------------------------------------------


def test_editing_the_stack_is_undoable(doc):
    path = doc.nodes.add(rect())
    assert doc.modifiers.add(path, "mirror", x=0) == 0
    assert doc.modifiers.add(path, "array", columns=2, dx=30) == 1
    assert doc.undo_text() == "Add array to bar"
    doc.modifiers.remove(path, 0)
    assert [m.kind for m in doc.node(path).modifiers] == ["array"]
    doc.undo()
    assert [m.kind for m in doc.node(path).modifiers] == ["mirror", "array"]
    with pytest.raises(ValueError, match="unknown modifier"):
        doc.modifiers.add(path, "twist")
    with pytest.raises(ValueError):
        doc.modifiers.update(path, 0, axis="z")
    assert doc.node(path).modifiers[0].axis == "x"  # unchanged


def test_placed_components_count_every_copy(doc):
    doc.components.new("post")
    doc.nodes.add(rect())
    doc.set_active("top")
    doc.nodes.add_component("post")
    doc.modifiers.add(((0, 0),), "mirror")
    doc.modifiers.add(((0, 0),), "array", columns=3)
    assert doc.components.placed("top") == [("post", 6)]


@pytest.mark.parametrize(
    "modifier",
    [
        ArrayModifier(columns=3, rows=2, dx=6, dy=8),
        PolarArrayModifier(count=5, x=3, y=-2),
        PolarArrayModifier(count=3, step=45, rotate=False),
        MirrorModifier(axis="both", x=-3, y=1),
    ],
    ids=["array", "polar", "polar-unrotated", "mirror"],
)
def test_apply_turns_a_modifier_into_real_shapes_without_moving_anything(doc, modifier):
    path = doc.nodes.add(rect(x1="12 + i", modifiers=[modifier, MirrorModifier(x=-40)]))
    before = region(doc)
    doc.modifiers.apply(path)
    applied = doc.node(path)
    assert isinstance(applied, TransformShape) and applied.name == "bar"
    assert [m.kind for m in applied.modifiers] == ["mirror"]  # the rest stays
    assert len(applied.children) == modifier.copies()
    assert (region(doc) ^ before).is_empty()
    doc.undo()
    assert doc.node(path).modifiers[0] == modifier


def test_applying_gives_named_parts_fresh_names(doc):
    doc.nodes.add(rect("pad"))
    doc.nodes.wrap([((0, 0),)], "transform")
    path = ((0, 0),)
    doc.modifiers.add(path, "array", columns=3, dx=20)
    doc.modifiers.apply(path)
    names = [child.children[0].name for child in doc.node(path).children]
    assert names == ["pad", "pad1", "pad2"]
