"""Design rules: project data checked by rule kinds, which are plugins
(requirements DRC-1, DRC-6 to DRC-12)."""

from typing import ClassVar

import klayout.db as kdb
import pytest

from mems_sketch import Layer, Project, RectShape
from mems_sketch.cli import main
from mems_sketch.core.component import Geometry, to_dbu
from mems_sketch.core.process import Rule, default_process
from mems_sketch.options import Option
from mems_sketch.process import rules
from mems_sketch.process.rules import Finding, check, rule_values
from mems_sketch.storage import load, save
from mems_sketch.storage.project_files import process_data, process_from_data


def box(x0, y0, x1, y1) -> kdb.Region:
    return kdb.Region(kdb.Box(to_dbu(x0), to_dbu(y0), to_dbu(x1), to_dbu(y1)))


def project(*rule_list: Rule, constants=None) -> Project:
    p = Project(name="p")
    for name, gds in (("device", 1), ("anchor", 2), ("metal", 3)):
        p.add_layer(Layer(name, gds))
    p.process.constants.update(constants or {})
    for rule in rule_list:
        p.process.add_rule(rule)
    return p


def violations(rule: Rule, constants=None, **layers: kdb.Region):
    geometry = Geometry()
    geometry.layers.update(layers)
    return check(project(rule, constants=constants), geometry)


def kinds_of(found):
    return [(v.kind, v.message) for v in found]


# -- the built-in kinds -----------------------------------------------------


def test_width_space_and_area():
    narrow = box(0, 0, 1, 10)
    assert violations(Rule("w", "min_width", ["device"], {"value": 2}), device=narrow)
    assert not violations(Rule("w", "min_width", ["device"], {"value": 1}), device=narrow)
    two = box(0, 0, 5, 5) + box(6, 0, 11, 5)
    assert violations(Rule("s", "min_space", ["device"], {"value": 2}), device=two)
    wide = violations(Rule("m", "max_width", ["device"], {"value": 4}), device=two)
    assert [v.message for v in wide] == ["wider than 4 µm"] * 2
    assert not violations(Rule("m", "max_width", ["device"], {"value": 5}), device=two)
    small = box(0, 0, 5, 5) + box(10, 0, 11, 1)
    [tiny] = violations(Rule("a", "min_area", ["device"], {"value": 2}), device=small)
    assert tiny.bbox_um == (10, 0, 11, 1)
    holed = box(0, 0, 10, 10) - box(4, 4, 5, 5)
    assert violations(Rule("h", "min_hole_area", ["device"], {"value": 2}), device=holed)
    assert not violations(Rule("h", "min_hole_area", ["device"], {"value": 1}), device=holed)


def test_pieces():
    two = box(0, 0, 5, 5) + box(6, 0, 11, 5)
    [found] = violations(Rule("c", "connected", ["device"]), device=two)
    assert found.message == "2 pieces, not 1"
    assert not violations(Rule("c", "connected", ["device"], {"pieces": 2}), device=two)


def test_two_layer_kinds():
    outer, inner = box(0, 0, 10, 10), box(1, 1, 9, 9)
    rule = Rule("e", "enclosure", ["metal", "device"], {"value": 2})
    assert {v.message for v in violations(rule, metal=outer, device=inner)} == {
        "enclosed by less than 2 µm"
    }
    assert not violations(
        Rule("e", "enclosure", ["metal", "device"], {"value": 1}), metal=outer, device=inner
    )
    outside = box(9, 9, 12, 12)
    assert "not enclosed" in {
        v.message
        for v in violations(
            Rule("e", "enclosure", ["metal", "device"], {"value": 0}), metal=outer, device=outside
        )
    }
    near = box(11, 0, 12, 10)
    assert violations(
        Rule("s", "separation", ["metal", "device"], {"value": 2}), metal=outer, device=near
    )
    assert violations(Rule("i", "inside", ["device", "metal"]), device=outside, metal=outer)
    assert not violations(Rule("i", "inside", ["device", "metal"]), device=inner, metal=outer)
    assert violations(
        Rule("o", "not_overlapping", ["device", "metal"]), device=outside, metal=outer
    )


def test_anchored_and_released():
    anchor = box(0, 0, 10, 10)
    beam = box(5, 4, 100, 7)  # 3 µm wide, held by the anchor
    floating = box(0, 50, 3, 53)
    found = violations(
        Rule("a", "anchored", ["device", "anchor"]), device=beam + floating, anchor=anchor
    )
    assert [v.bbox_um for v in found] == [(0, 50, 3, 53)]

    release = Rule("r", "release", ["device", "anchor"], {"undercut": "process.undercut"})
    pad = box(0, 0, 30, 40)  # a 30 µm anchor pad holding a 20 µm wide plate
    device = pad + box(30, 10, 50, 30)
    [stuck] = violations(release, {"undercut": 2}, device=device, anchor=pad)
    assert "not released" in stuck.message and stuck.bbox_um[0] >= 30
    assert not violations(release, {"undercut": 11}, device=device, anchor=pad)
    narrow = box(0, 0, 30, 20)  # an anchor only 20 µm wide: an 11 µm undercut frees it
    found = violations(release, {"undercut": 11}, device=box(0, 0, 30, 20), anchor=narrow)
    assert [v.message for v in found] == ["anchor narrower than 2 × 11 µm: undercut frees it"]


# -- rules as data ------------------------------------------------------------


def test_values_are_expressions_over_the_process():
    rule = Rule("w", "min_width", ["device"], {"value": "2 * process.min_gap"})
    narrow = box(0, 0, 3, 10)
    [found] = violations(rule, {"min_gap": 2}, device=narrow)
    assert found.values == {"value": 4.0} and found.message == "width < 4 µm"
    assert not violations(rule, {"min_gap": 1}, device=narrow)


def test_rules_that_cannot_be_checked_never_pass():
    narrow = box(0, 0, 1, 10)
    cases = {
        "not installed": Rule("x", "no_such_kind", ["device"]),
        "is not defined": Rule("x", "min_width", ["oxide"]),
        "takes 1 layer": Rule("x", "min_width", ["device", "metal"]),
        "no parameter 'width'": Rule("x", "min_width", ["device"], {"width": 1}),
        "unknown variable": Rule("x", "min_width", ["device"], {"value": "process.nothing"}),
        "at least 0": Rule("x", "min_width", ["device"], {"value": -1}),
    }
    for expected, rule in cases.items():
        [found] = violations(rule, device=narrow)
        assert found.is_error and expected in found.message, (expected, found.message)


def test_rules_turned_off_and_severity():
    narrow = box(0, 0, 1, 10)
    off = Rule("w", "min_width", ["device"], {"value": 2}, enabled=False)
    assert not violations(off, device=narrow)
    warning = Rule("w", "min_width", ["device"], {"value": 2}, severity="warning")
    [found] = violations(warning, device=narrow)
    assert not found.is_error and rules.warnings([found]) == [found]
    noted = Rule("w", "min_width", ["device"], {"value": 2}, message="fab limit")
    assert violations(noted, device=narrow)[0].message.endswith("(fab limit)")
    with pytest.raises(ValueError, match="severity"):
        Rule("w", "min_width", ["device"], severity="fatal")


def test_rules_in_the_process_file_and_older_files():
    process = default_process()
    data = process_data(process)
    assert data["rules"]["device_release"] == {
        "kind": "release",
        "layers": ["device", "anchor"],
        "undercut": "process.undercut",
        "severity": "warning",
    }
    assert process_from_data(data).rules == process.rules
    older = {"layers": {"device": {"gds": [1, 0], "min_width": 2, "min_space": 3}}}
    migrated = process_from_data(older).rules
    assert migrated["device_min_width"].values == {"value": 2.0}
    assert migrated["device_min_space"].kind == "min_space"


def test_a_project_keeps_its_rules(tmp_path):
    p = project(Rule("w", "min_width", ["device"], {"value": 2}, enabled=False, message="m"))
    save(p, tmp_path / "p")
    assert load(tmp_path / "p").process.rules == p.process.rules


# -- plugins ------------------------------------------------------------------


class _Square:
    """A plugin kind: every piece must be square."""

    name = "square"
    title = "Square"
    roles = ("layer",)
    parameters: ClassVar = (Option("tolerance", 0.0, "Tolerance", minimum=0),)

    def check(self, regions, dbu, tolerance):
        return [
            Finding("not square", (p.bbox().left * dbu, 0, 0, 0))
            for p in regions[0].merged().each()
            if abs(p.bbox().width() - p.bbox().height()) * dbu > tolerance
        ]


@pytest.fixture
def square(monkeypatch):
    monkeypatch.setitem(rules._runtime, "square", _Square)


def test_a_plugin_kind(square):
    rule = Rule("sq", "square", ["device"], {"tolerance": 0.5})
    assert not violations(rule, device=box(0, 0, 3, 3.4))
    assert kinds_of(violations(rule, device=box(0, 0, 3, 4))) == [("square", "not square")]
    assert rule_values(_Square, Rule("sq", "square", ["device"])) == {"tolerance": 0.0}


def test_the_cli_lists_kinds_and_fails_only_on_errors(square, tmp_path, capsys):
    p = project(Rule("big", "max_width", ["device"], {"value": 1}, severity="warning"))
    p.add(RectShape(name="r", layer="device", x0=0, y0=0, x1=5, y1=5))
    save(p, tmp_path / "p")
    assert main(["check", str(tmp_path / "p")]) == 0
    assert "0 error(s), 1 warning(s)" in capsys.readouterr().err
    assert main(["check", str(tmp_path / "p"), "--strict"]) == 1
    assert main(["rules"]) == 0
    out = capsys.readouterr().out
    assert "release" in out and "undercut=1.0 µm" in out and "square" in out
    assert main(["info", str(tmp_path / "p")]) == 0
    assert "big" in capsys.readouterr().out
