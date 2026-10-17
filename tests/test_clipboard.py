"""Copy, cut and paste shapes, also into another component (#136)."""

import pytest

from mems_sketch import RectShape
from mems_sketch.core.shapes import RefShape
from mems_sketch.editing import EditSession
from mems_sketch.editing.clipboard import is_shapes


def bar(name="bar", **fields):
    return RectShape(name=name, layer="device", **({"x0": 0, "y0": 0, "x1": 10, "y1": 2} | fields))


@pytest.fixture
def doc() -> EditSession:
    session = EditSession()
    session.parameters.set("w", 4.0, min=1)
    session.parameters.set("h", "w / 2")
    session.nodes.add(bar(x1="w", y1="h"))
    session.nodes.add(bar("tag", x0="bar.right.x", x1="bar.right.x + 1"))
    return session


def names(doc) -> list[str]:
    return [s.name for s in doc.shapes]


def test_copied_shapes_paste_with_fresh_names_each_time(doc):
    text = doc.clipboard.copy([((0, 0),), ((0, 1),)])
    assert is_shapes(text) and not is_shapes("bar: 1")
    first = doc.clipboard.paste(text)
    doc.clipboard.paste(text)
    assert names(doc) == ["bar", "tag", "bar1", "tag1", "bar2", "tag2"]
    assert first == [((0, 2),), ((0, 3),)]
    assert doc.node(((0, 3),)).x0 == "bar1.right.x"  # still follows its own copy


def test_pasting_into_another_component_brings_the_parameters_it_uses(doc):
    text = doc.clipboard.copy([((0, 0),)])
    doc.components.new("other")
    doc.parameters.set("w", 6.0)  # here already, with another default
    pasted = doc.clipboard.paste(text)
    params = {p.name: p.default for p in doc.active_definition.parameters}
    assert params == {"w": 6.0, "h": "w / 2"}  # h added; w kept
    assert pasted.notes and "'w'" in pasted.notes[0]
    assert names(doc) == ["bar"]


def test_cut_and_paste_are_one_undo_step_each(doc):
    text = doc.clipboard.cut([((0, 1),)])
    assert names(doc) == ["bar"]
    doc.clipboard.paste(text)
    assert names(doc) == ["bar", "tag"]
    doc.undo()
    assert names(doc) == ["bar"]
    doc.undo()
    assert names(doc) == ["bar", "tag"]


def test_a_shape_inside_a_selected_one_is_copied_with_it_only(doc):
    doc.nodes.wrap([((0, 0),), ((0, 1),)], "transform")
    text = doc.clipboard.copy([((0, 0),), ((0, 0), (0, 1))])
    doc.clipboard.paste(text)
    assert len(doc.shapes) == 2


def test_a_private_component_cannot_be_pasted_where_it_cannot_be_placed(doc):
    owner = doc.active
    doc.components.make([((0, 0),), ((0, 1),)], "cell")  # private to the component it came from
    path = next(((0, k),) for k, s in enumerate(doc.shapes) if isinstance(s, RefShape))
    text = doc.clipboard.copy([path])
    doc.clipboard.paste(text)  # where it came from: fine
    assert sum(isinstance(s, RefShape) for s in doc.shapes) == 2
    doc.components.new("other")
    with pytest.raises(ValueError, match="private to"):
        doc.clipboard.paste(text)
    assert doc.shapes == [] and owner != "other"


def test_text_that_is_not_shapes_is_refused(doc):
    with pytest.raises(ValueError, match="no shapes"):
        doc.clipboard.paste("hello")
