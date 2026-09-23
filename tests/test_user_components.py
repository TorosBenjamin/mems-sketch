import klayout.db as kdb
import pytest
from pydantic import ValidationError

from mems_sketch import (
    ComponentDef,
    Design,
    Instance,
    Layer,
    ParamDef,
    PolygonShape,
    RectShape,
    RefShape,
    Repeat,
    load,
    save,
)
from mems_sketch.core.component import to_dbu


def finger_array() -> ComponentDef:
    """n fingers of width w on a pitch, each one `taper` longer than the last."""
    return ComponentDef(
        name="finger_array",
        parameters=[
            ParamDef(name="n", default=4, min=1, integer=True),
            ParamDef(name="w", default=2, min=0.5),
            ParamDef(name="pitch", default=6),
            ParamDef(name="length", default=30),
            ParamDef(name="taper", default=0),
        ],
        shapes=[
            RectShape(
                layer="device",
                x0=0,
                y0=0,
                x1="w",
                y1="length + i * taper",
                repeat=Repeat(columns="n", dx="pitch"),
            ),
            RectShape(layer="device", x0=0, y0=-5, x1="(n - 1) * pitch + w", y1=0),
        ],
    )


def triangle() -> ComponentDef:
    return ComponentDef(
        name="triangle",
        parameters=[ParamDef(name="base", default=20), ParamDef(name="height", default=10)],
        shapes=[PolygonShape(layer="device", points=[(0, 0), ("base", 0), ("base / 2", "height")])],
    )


def make_design() -> Design:
    design = Design(name="custom")
    design.add_layer(Layer("device", 1))
    design.add_layer(Layer("anchor", 2))
    design.set_variable("w_global", 3.0)
    design.define_component(finger_array())
    design.define_component(triangle())
    return design


def area_um2(region: kdb.Region) -> float:
    return region.area() / to_dbu(1) ** 2


def test_rect_repeat_and_expressions():
    design = make_design()
    design.add_instance(Instance("f", "finger_array", {"n": 5, "w": "w_global"}))
    device = design.render().layers["device"]
    # 5 fingers 3 x 30 plus a spine of (4*6+3) x 5
    assert area_um2(device) == pytest.approx(5 * 3 * 30 + 27 * 5)


def test_repeat_index_drives_taper():
    design = make_design()
    design.add_instance(Instance("f", "finger_array", {"n": 3, "taper": 10}))
    box = design.render().layers["device"].bbox()
    assert box.top == to_dbu(30 + 2 * 10)


def test_polygon_shape():
    design = make_design()
    design.add_instance(Instance("t", "triangle", {"base": 10, "height": 4}))
    assert area_um2(design.render().layers["device"]) == pytest.approx(20)


def test_nested_refs_to_user_and_builtin_components():
    design = make_design()
    design.define_component(
        ComponentDef(
            name="pair",
            parameters=[ParamDef(name="spacing", default=100)],
            shapes=[
                RefShape(component="triangle", params={"base": 10}),
                RefShape(component="triangle", params={"base": 10}, x="spacing", rotation=180),
                RefShape(component="anchor", params={"size": 20}, x="spacing / 2"),
            ],
        )
    )
    design.add_instance(Instance("p", "pair", {"spacing": 50}))
    geometry = design.render()
    assert geometry.layers["device"].count() == 3
    assert geometry.layers["anchor"].count() == 1


def test_parameter_limits_are_enforced():
    design = make_design()
    with pytest.raises(ValidationError):
        design.add_instance(Instance("f", "finger_array", {"n": 0}))
    with pytest.raises(ValidationError):
        design.add_instance(Instance("f", "finger_array", {"n": 2.5}))


def test_invalid_definitions_are_rejected():
    design = make_design()
    with pytest.raises(ValueError, match="built-in"):
        design.define_component(ComponentDef(name="anchor"))
    with pytest.raises(ValueError, match="unknown component"):
        design.define_component(ComponentDef(name="x", shapes=[RefShape(component="nope")]))
    with pytest.raises(ValueError, match="circular"):
        design.define_component(ComponentDef(name="a", shapes=[RefShape(component="a")]))
    with pytest.raises(ValidationError):
        ParamDef(name="i", default=1)
    with pytest.raises(ValidationError):
        ParamDef(name="w", default=0, min=1)
    assert set(design.components) == {"finger_array", "triangle"}  # nothing half-added


def test_cycle_through_redefinition_is_rejected_and_rolled_back():
    design = make_design()
    design.define_component(ComponentDef(name="a", shapes=[RefShape(component="triangle")]))
    design.define_component(ComponentDef(name="b", shapes=[RefShape(component="a")]))
    with pytest.raises(ValueError, match="circular"):
        design.define_component(ComponentDef(name="a", shapes=[RefShape(component="b")]))
    assert design.components["a"].references() == {"triangle"}


def test_remove_component_in_use_is_refused():
    design = make_design()
    design.add_instance(Instance("t", "triangle"))
    with pytest.raises(ValueError, match="still used"):
        design.remove_component("triangle")
    design.remove_component("finger_array")
    assert "finger_array" not in design.components


def test_user_components_round_trip_through_sqlite(tmp_path):
    design = make_design()
    design.add_instance(Instance("f", "finger_array", {"n": 3, "taper": "w_global"}))
    save(design, tmp_path / "custom.mems")
    loaded = load(tmp_path / "custom.mems")
    assert loaded == design
    assert loaded.render().layers["device"].area() == design.render().layers["device"].area()
