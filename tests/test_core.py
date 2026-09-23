import klayout.db as kdb
import pytest
from pydantic import ValidationError

from mems_sketch import Design, Instance, Layer, export, load, save
from mems_sketch.core.component import to_dbu
from mems_sketch.core.expressions import ExpressionError, evaluate, resolve_variables
from mems_sketch.export.base import available_exporters
from mems_sketch.process import etch, rules


def make_design() -> Design:
    design = Design(name="accel")
    design.add_layer(Layer("device", 1, 0, undercut=0.5, min_width=1.5, min_space=1.5))
    design.add_layer(Layer("anchor", 2, 0))
    design.set_variable("w", 2.0)
    design.set_variable("gap", "w * 1.5")
    design.add_instance(Instance("pad", "anchor", {"size": 50}, x=-200))
    design.add_instance(Instance("comb", "comb_drive", {"finger_width": "w", "gap": "gap"}))
    return design


def test_expressions():
    assert evaluate("2 * a + max(1, b)", {"a": 3, "b": 4}) == 10
    assert resolve_variables({"b": "a * 2", "a": 1.5}) == {"a": 1.5, "b": 3.0}
    with pytest.raises(ExpressionError):
        resolve_variables({"a": "b", "b": "a"})
    with pytest.raises(ExpressionError):
        evaluate("__import__('os')", {})


def test_params_resolve_expressions_and_validate():
    design = make_design()
    params = design.resolve_params(design.instance("comb"))
    assert params.finger_width == 2.0 and params.gap == 3.0
    with pytest.raises(ValidationError):
        design.add_instance(Instance("bad", "anchor", {"size": 4, "enclosure": 3}))


def test_render_and_variable_change_updates_geometry():
    design = make_design()
    before = design.render().layers["device"].area()
    design.set_variable("w", 3.0)
    assert design.render().layers["device"].area() > before


def test_rotation_and_placement():
    design = Design()
    design.add_instance(
        Instance("r", "rectangle", {"width": 100, "height": 10}, x=50, y=0, rotation=90)
    )
    box = design.render().layers["device"].bbox()
    assert box == kdb.Box(to_dbu(45), to_dbu(-50), to_dbu(55), to_dbu(50))


def test_etch_loss_shrinks_and_compensation_grows():
    design = make_design()
    drawn = design.render().layers["device"].area()
    assert etch.etched(design).layers["device"].area() < drawn
    assert etch.compensated(design).layers["device"].area() > drawn
    # Undercut 0 on the anchor layer leaves it untouched.
    assert etch.etched(design).layers["anchor"].area() == design.render().layers["anchor"].area()


def test_rules_flag_narrow_features_and_unknown_layers():
    design = make_design()
    assert rules.check(design) == []
    design.set_variable("w", 1.0)  # fingers now narrower than min_width
    found = {v.rule for v in rules.check(design)}
    assert "min_width" in found
    design.add_instance(Instance("m", "rectangle", {"layer": "metal"}))
    assert any(v.rule == "layer" and v.layer == "metal" for v in rules.check(design))


def test_sqlite_round_trip(tmp_path):
    design = make_design()
    path = tmp_path / "accel.mems"
    save(design, path)
    save(design, path)  # overwriting an existing file works
    loaded = load(path)
    assert loaded == design
    assert loaded.render().layers["device"].area() == design.render().layers["device"].area()


@pytest.mark.parametrize("suffix", [".gds", ".oas", ".dxf"])
def test_export_formats(tmp_path, suffix):
    design = make_design()
    path = export(design, tmp_path / f"accel{suffix}")
    layout = kdb.Layout()
    layout.read(str(path))
    assert layout.top_cell().bbox().width() > 0


def test_exporters_are_pluggable():
    assert {"gds", "oasis", "dxf"} <= set(available_exporters())
