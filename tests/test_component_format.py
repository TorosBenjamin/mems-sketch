"""Component files: maps by name, placements with their values beside them (PRJ-3)."""

import pytest

from mems_sketch import (
    BooleanShape,
    ComponentDef,
    Layer,
    ParamDef,
    PointDef,
    Project,
    RectShape,
    RefShape,
    load,
    save,
)
from mems_sketch.storage import yaml_format
from mems_sketch.storage.component_format import component_data, component_from_data


def roundtrip(definition: ComponentDef) -> ComponentDef:
    text = yaml_format.dump(component_data(definition))
    again, _ = component_from_data(definition.name, yaml_format.load(text))
    return again


def test_every_shape_gets_a_name():
    definition = ComponentDef(
        name="c",
        shapes=[
            RectShape(x0=0, y0=0, x1=1, y1=1),
            RectShape(name="rect1", x0=2, y0=0, x1=3, y1=1),
            RefShape(component="std.anchor"),
            BooleanShape(op="subtract", a=[RectShape(x0=0, y0=0, x1=1, y1=1)], b=[]),
        ],
    )
    names = [s.name for s in definition.shapes]
    assert names == ["rect2", "rect1", "anchor1", "boolean1"]
    assert definition.shapes[3].a[0].name == "rect3"


def test_maps_by_name_and_values_beside_the_reference():
    definition = ComponentDef(
        name="c",
        parameters=[ParamDef(name="w", default=2), ParamDef(name="n", default=3, integer=True)],
        points=[PointDef(name="tip", at="arm.right", x=1)],
        shapes=[
            RefShape(name="arm", component="beam", params={"length": "10 * w"}, rotation=90),
            RefShape(name="odd", component="beam", params={"x": 5, "length": 1}),
            BooleanShape(
                name="cut",
                op="subtract",
                a=[RectShape(name="plate", x0=0, y0=0, x1=10, y1=10)],
                b=[RectShape(name="hole", x0=4, y0=4, x1=6, y1=6)],
            ),
        ],
    )
    data = component_data(definition)
    assert data["parameters"] == {"w": 2, "n": {"default": 3, "integer": True}}
    assert data["points"] == {"tip": {"at": "arm.right", "x": 1}}
    assert data["shapes"]["arm"] == {"ref": "beam", "length": "10 * w", "rotation": 90}
    # A parameter named like a field of the placement: its values under params.
    assert data["shapes"]["odd"] == {"ref": "beam", "params": {"x": 5, "length": 1}}
    assert list(data["shapes"]["cut"]["a"]) == ["plate"]
    assert roundtrip(definition) == definition


def test_files_that_are_not_maps_are_refused():
    with pytest.raises(ValueError, match="map by name"):
        component_from_data("c", {"shapes": [{"kind": "rect"}]})
    with pytest.raises(ValueError, match="neither a kind nor a ref"):
        component_from_data("c", {"shapes": {"s": {"x0": 1}}})
    with pytest.raises(ValueError, match="unknown field 'name'"):
        component_from_data("c", {"name": "c"})


def test_private_components_nest_in_a_document(tmp_path):
    project = Project(name="p")
    project.add_layer(Layer("device", 1))
    project.define_component(ComponentDef(name="comb"))
    project.define_component(ComponentDef(name="comb/finger"))
    project.define_component(
        ComponentDef(
            name="comb/finger/tip", shapes=[RectShape(layer="device", x0=0, y0=0, x1=1, y1=1)]
        )
    )
    save(project, tmp_path / "p.yaml")
    text = (tmp_path / "p.yaml").read_text()
    assert "  comb:\n    private:\n      finger:\n        private:\n          tip:" in text
    assert load(tmp_path / "p.yaml") == project
