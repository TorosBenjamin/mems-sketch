"""Internal parameters: used only by their component, never set where it is placed."""

import pytest

from mems_sketch.core.shapes import RectShape, RefShape
from mems_sketch.core.user_component import ParamDef
from mems_sketch.editing import EditSession
from mems_sketch.storage.yaml_format import to_data


def area(doc: EditSession) -> float:
    region = doc.results.geometry("drawn").layers.get("device")
    return 0.0 if region is None else region.area() / 1e6


@pytest.fixture
def doc() -> EditSession:
    doc = EditSession()
    doc.components.new("pad")
    doc.parameters.set("size", 20.0)
    doc.parameters.set("inner", "size / 2")
    doc.nodes.add(RectShape(layer="device", x0=0, y0=0, x1="size", y1="inner"))
    doc.parameters.set_internal(["inner"])
    doc.set_active("top")
    return doc


def test_an_internal_parameter_keeps_its_default_where_placed(doc):
    doc.nodes.add(RefShape(name="p", component="pad", params={"size": 10}))
    assert area(doc) == pytest.approx(10 * 5)
    assert doc.component("pad").internal == {"inner"}
    assert list(doc.component("pad").public_params()) == ["size"]


def test_setting_an_internal_parameter_where_placed_is_refused(doc):
    with pytest.raises(ValueError, match="'inner' of component 'pad' is internal"):
        doc.nodes.add(RefShape(name="p", component="pad", params={"inner": 3}))
    assert doc.shapes == []


def test_making_a_parameter_internal_that_a_placement_sets_is_refused(doc):
    doc.nodes.add(RefShape(name="p", component="pad", params={"size": 10}))
    doc.set_active("pad")
    with pytest.raises(ValueError, match="internal"):
        doc.parameters.set_internal(["size"])
    assert not doc.active_definition.parameter("size").internal


def test_internal_and_public_again_is_one_undo_step_each(doc):
    doc.set_active("pad")
    doc.parameters.set_internal(["size", "inner"])
    assert all(p.internal for p in doc.active_definition.parameters)
    doc.parameters.set_internal(["size", "inner"], internal=False)
    assert not any(p.internal for p in doc.active_definition.parameters)
    doc.undo()
    assert all(p.internal for p in doc.active_definition.parameters)


def test_trial_values_still_work_inside_the_component(doc):
    doc.set_active("pad")
    doc.set_trial("inner", 1.0)
    assert area(doc) == pytest.approx(20 * 1)


def test_saved_only_when_set():
    assert "internal" not in to_data(ParamDef(name="a"))
    assert to_data(ParamDef(name="a", internal=True))["internal"] is True


def test_make_component_passes_internal_values_in_as_public(doc):
    doc.parameters.set("w", 4.0)
    doc.parameters.set_internal(["w"])
    doc.nodes.add(RectShape(layer="device", x0=0, y0=0, x1="w", y1=1))
    path = doc.components.make([((0, 0),)], "cell")
    assert doc.node(path).params == {"w": "w"}
    assert not doc.project.components["cell"].parameter("w").internal
    assert area(doc) == pytest.approx(4)
