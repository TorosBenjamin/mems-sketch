"""Rule check messages say what was measured against the limit and which shape
it is on (#210); a beam exactly twice the undercut wide releases at any angle
(#211)."""

import json
import math

import pytest

from mems_sketch import ComponentDef, Layer, Project
from mems_sketch.cli import main
from mems_sketch.core.process import Rule
from mems_sketch.core.shapes import PathShape, RectShape
from mems_sketch.process import rules
from mems_sketch.storage import save


def project(*shapes, rule_list=()) -> Project:
    p = Project(name="p")
    for name, gds in (("device", 1), ("anchor", 2)):
        p.add_layer(Layer(name, gds))
    for rule in rule_list:
        p.process.add_rule(rule)
    p.components["top"] = ComponentDef(name="top", shapes=list(shapes))
    p.top = "top"
    return p


RELEASE = Rule("release", "release", ["device", "anchor"], {"undercut": 2})
WIDTH = Rule("width", "min_width", ["device"], {"value": 2})


def anchored_pad() -> list:
    return [
        RectShape(name="pad", layer="anchor", x0=-12, y0=-12, x1=12, y1=12),
        RectShape(name="pad_top", layer="device", x0=-12, y0=-12, x1=12, y1=12),
    ]


def released(width: float, angle: float) -> list:
    a = math.radians(angle)
    beam = PathShape(
        name="beam",
        layer="device",
        points=[(0.0, 0.0), (137.3 * math.cos(a), 137.3 * math.sin(a))],
        width=width,
    )
    p = project(beam, *anchored_pad(), rule_list=[RELEASE])
    return rules.check(p, component="top")


@pytest.mark.parametrize("angle", [0, 51, 58, 141, 190, 210, 232, 299])
def test_a_beam_exactly_twice_the_undercut_releases_at_any_angle(angle):
    assert released(4, angle) == []


def test_a_beam_a_rounding_wider_still_passes_but_a_wider_one_does_not():
    assert released(4.002, 190) == []
    [stuck] = released(4.05, 190)
    assert stuck.measured == pytest.approx(4.05, abs=0.005)
    assert stuck.message.startswith("not released: 4.0")
    assert "µm wide > 4 µm (2 × 2 µm undercut)" in stuck.message


def test_a_stuck_part_is_told_how_to_fix_it_whether_it_moves_or_stays():
    plate = RectShape(name="plate", layer="device", x0=50, y0=-10, x1=70, y1=10)
    p = project(plate, *anchored_pad(), rule_list=[RELEASE])
    [found] = rules.check(p, component="top")
    assert found.message.startswith("not released: 20 µm wide > 4 µm (2 × 2 µm undercut)")
    assert "narrow it or add release holes" in found.message
    assert "if it should stay fixed (a pad, a wire, a frame), anchor it" in found.message


def test_material_only_along_an_anchors_edge_is_its_foot():
    plate = RectShape(name="plate", layer="device", x0=12, y0=-10, x1=40, y1=10)
    p = project(plate, *anchored_pad(), rule_list=[RELEASE])
    [found] = rules.check(p, component="top")  # the plate, not the pad's rim
    assert found.bbox_um[0] >= 12 and found.measured == pytest.approx(20, abs=0.01)


def test_violations_name_the_shapes_they_are_on():
    thin = PathShape(name="thin", layer="device", points=[(0.0, 0.0), (0.0, 80.0)], width=1.5)
    p = project(thin, *anchored_pad(), rule_list=[WIDTH])
    found = rules.check(p, component="top")
    named = rules.name_shapes(found, p, "top", rules.records_of(p, "top"))
    [narrow] = named
    assert narrow.message == "1.5 µm wide < 2 µm" and narrow.measured == pytest.approx(1.5)
    assert narrow.shapes == ("thin",)  # not pad_top, which it only touches


def test_the_command_line_says_the_shape_and_the_measured_value(tmp_path, capsys):
    thin = PathShape(name="thin", layer="device", points=[(0.0, 0.0), (0.0, 80.0)], width=1.5)
    save(project(thin, *anchored_pad(), rule_list=[WIDTH]), tmp_path / "p")
    assert main(["check", str(tmp_path / "p")]) == 1
    out = capsys.readouterr().out
    assert "1.5 µm wide < 2 µm" in out and "on thin" in out
    assert main(["check", str(tmp_path / "p"), "--json"]) == 1
    [entry] = json.loads(capsys.readouterr().out)
    assert entry["shapes"] == ["thin"] and entry["measured_um"] == pytest.approx(1.5)


def test_the_mcp_check_says_the_shape_and_the_measured_value(tmp_path):
    from mems_sketch.mcp_server.tools import DesignTools

    thin = PathShape(name="thin", layer="device", points=[(0.0, 0.0), (0.0, 80.0)], width=1.5)
    save(project(thin, *anchored_pad(), rule_list=[WIDTH]), tmp_path / "p")
    tools = DesignTools()
    tools.open_project(str(tmp_path / "p"))
    [entry] = tools.check("top")["violations"]
    assert entry["shapes"] == ["thin"] and entry["measured_um"] == pytest.approx(1.5)
