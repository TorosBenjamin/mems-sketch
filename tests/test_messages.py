"""What the editor says when it refuses something (#252): in words, with where the
problem is, never a validation library's dump."""

import pytest

from mems_sketch.core.errors import explain
from mems_sketch.core.shapes import Align, PolygonShape, RectShape
from mems_sketch.editing import EditSession


@pytest.fixture
def doc() -> EditSession:
    session = EditSession()
    session.parameters.set("w", 10, min=1)
    session.nodes.add(RectShape(name="a", layer="device", x0=0, y0=0, x1=10, y1=10))
    return session


def said(action) -> str:
    with pytest.raises(Exception) as caught:
        action()
    text = explain(caught.value)
    assert "validation error" not in text and "pydantic" not in text and "[type=" not in text
    return text


def rect(x1) -> RectShape:
    return RectShape(name="a", layer="device", x0=0, y0=0, x1=x1, y1=10)


def test_parameter_values_say_what_is_wrong(doc):
    doc.parameters.set("n", 2, integer=True)
    assert said(lambda: doc.set_trial("w", 0)) == "parameter 'w' of 'top' must be at least 1, not 0"
    assert said(lambda: doc.set_trial("bogus", 5)) == (
        "'top' has no parameter 'bogus' (its parameters: w, n)"
    )
    assert "must be a whole number, not 2.5" in said(
        lambda: doc.parameters.update("n", default="w / 4")
    )
    assert said(lambda: doc.parameters.update("w", min=5, max=1)) == (
        "the min of 'w' (5) is more than its max (1)"
    )
    assert "no parameter 'nonexistent'" in said(lambda: doc.parameters.remove("nonexistent"))


def test_a_trial_value_may_use_the_other_parameters(doc):
    doc.parameters.set("gap", "w / 2")
    doc.set_trial("gap", "w * 3")
    assert doc.results.scope()["gap"] == 30


def test_expression_errors_say_the_shape_and_the_field(doc):
    replace = lambda x1: doc.nodes.replace(((0, 0),), rect(x1))
    assert "'a': x1: w/0: division by zero" in said(lambda: replace("w/0"))
    assert "x1: 0*1e400 is not a finite number" in said(lambda: replace("0*1e400"))
    called = said(lambda: replace("log(2)"))
    assert "'a': x1: log(2)" in called and "sind, cosd, tand" in called
    assert "circular: 'a' depends on its own points" in said(lambda: replace("a.right.x"))


def test_fields_are_named_in_plain_words(doc):
    def two_points():
        return PolygonShape(name="p", layer="device", points=[(0, 0), (1, 1)])

    assert said(two_points) == "points: needs at least 3 items, not 2"
    assert "its direction is right, up, left, down or an angle, not 'north'" in said(
        lambda: doc.ports.add(at="a.right", name="o", direction="north")
    )


def test_a_refused_delete_says_what_still_uses_the_shape(doc):
    post = RectShape(
        name="post",
        layer="device",
        x0=0,
        y0=0,
        x1=5,
        y1=5,
        align=Align(point="bottom_left", to="a.top_left"),
    )
    doc.nodes.add(post)
    assert (
        said(lambda: doc.nodes.remove([((0, 0),)])) == "can't delete 'a': 'post' is aligned to it"
    )
    doc.nodes.remove([((0, 1),)])
    doc.nodes.add(RectShape(name="bar", layer="device", x0=0, y0=0, x1="a.right.x", y1=5))
    assert said(lambda: doc.nodes.remove([((0, 0),)])) == "can't delete 'a': 'bar' uses its points"


def test_the_command_line_says_it_in_words(tmp_path, capsys):
    from pathlib import Path

    from mems_sketch.cli import main

    example = Path(__file__).parent.parent / "examples" / "resonator"
    assert main(["check", str(example), "--set", "bogus=1"]) == 2
    error = capsys.readouterr().err
    assert "'top' has no parameter 'bogus'" in error and "pydantic" not in error
    assert main(["check", str(example), "--set", "pitch=plate/16"]) in (0, 1)  # uses another
