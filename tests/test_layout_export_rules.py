"""Layouts that keep the hierarchy (#74) and the last built-in rule kinds:
minimum angle, density and overlap (#65, #104)."""

import pytest

from mems_sketch import ArrayModifier, Layer, Project, RefShape, layout
from mems_sketch.core.component import Geometry, to_dbu
from mems_sketch.core.process import Rule
from mems_sketch.core.region import Box, IntPolygon, Region
from mems_sketch.editing import EditSession
from mems_sketch.export.base import export
from mems_sketch.export.hierarchy import _arrays, cell_name


def box(x0, y0, x1, y1) -> Region:
    return Region(Box(to_dbu(x0), to_dbu(y0), to_dbu(x1), to_dbu(y1)))


def project(rule: Rule) -> Project:
    p = Project(name="p")
    for name, gds in (("device", 1), ("anchor", 2), ("metal", 3)):
        p.add_layer(Layer(name, gds))
    p.process.add_rule(rule)
    return p


def violations(rule: Rule, **layers: Region):
    from mems_sketch.process.rules import check

    geometry = Geometry()
    geometry.layers.update(layers)
    return check(project(rule), geometry)


def triangle(*points) -> Region:
    return Region(IntPolygon([(to_dbu(x), to_dbu(y)) for x, y in points]))


# -- #65: minimum angle ------------------------------------------------------------------


def test_a_spike_sharper_than_the_angle():
    spike = triangle((0, 0), (20, 0), (0, 4))  # ~11° at (20, 0)
    [found] = violations(Rule("a", "min_angle", ["device"], {"angle": 30}), device=spike)
    assert found.message.startswith("11.3") and found.message.endswith("° corner < 30°")
    assert found.bbox_um == pytest.approx((19.5, -0.5, 20.5, 0.5))
    assert not violations(Rule("a", "min_angle", ["device"], {"angle": 10}), device=spike)


def test_right_angles_curves_and_notches_pass():
    square = box(0, 0, 10, 10)
    assert not violations(Rule("a", "min_angle", ["device"], {"angle": 89}), device=square)
    notch = box(0, 0, 20, 10) - triangle((9, 10), (11, 10), (10, 2))  # a sharp gap, not a spike
    assert not violations(Rule("a", "min_angle", ["device"], {"angle": 30}), device=notch)
    # A hole's corners: the material around a diamond hole has wide angles.
    holed = box(0, 0, 20, 20) - triangle((5, 10), (10, 15), (15, 10))
    assert not violations(Rule("a", "min_angle", ["device"], {"angle": 30}), device=holed)


# -- #65: density -------------------------------------------------------------------------


def test_density_of_the_whole_extent_and_of_windows():
    quarter = box(0, 0, 10, 10)
    frame = box(0, 0, 20, 20) - box(0.5, 0.5, 19.5, 19.5)  # sets the extent: 20 × 20
    layers = {"device": quarter, "metal": frame}
    rule = Rule("d", "density", ["device"], {"minimum": 30, "maximum": 80})
    [found] = violations(rule, **layers)
    assert found.message == "density 25 % < 30 %" and found.measured == 25
    assert not violations(Rule("d", "density", ["device"], {"minimum": 20}), **layers)
    windows = Rule("d", "density", ["device"], {"maximum": 50, "window": 10})
    [full] = violations(windows, **layers)  # the window it fills
    assert full.bbox_um == (0, 0, 10, 10) and "100 % > 50 % in this window" in full.message
    assert (
        len(violations(Rule("d", "density", ["device"], {"minimum": 50, "window": 10}), **layers))
        == 3
    )


def test_too_many_windows_is_the_rules_problem():
    rule = Rule("d", "density", ["device"], {"minimum": 1, "window": 0.01})
    [problem] = violations(rule, device=box(0, 0, 100, 100))
    assert "not checked; take a larger window" in problem.message


# -- #65, #104: overlap -------------------------------------------------------------------


def test_overlap_by_at_least_a_width():
    gate, active = box(0, 0, 10, 4), box(9, -5, 20, 9)  # 1 µm overlap
    [short] = violations(
        Rule("o", "overlap", ["metal", "device"], {"value": 2}), metal=gate, device=active
    )
    assert short.message == "overlap 1 µm < 2 µm" and short.measured == 1
    assert not violations(
        Rule("o", "overlap", ["metal", "device"], {"value": 1}), metal=gate, device=active
    )
    touching = box(-5, 0, 9, 4)  # meets the active area at x = 9 without overlapping
    found = violations(
        Rule("o", "overlap", ["metal", "device"], {"value": 1}), metal=touching, device=active
    )
    assert [v.message for v in found] == ["touches without overlapping"]
    apart = box(-5, 0, 5, 4)  # does not meet it at all: not this rule's business
    assert not violations(
        Rule("o", "overlap", ["metal", "device"], {"value": 1}), metal=apart, device=active
    )


def test_the_new_kinds_are_listed():
    from mems_sketch.process.rules import available_rule_kinds

    names = set(available_rule_kinds())
    assert {"min_angle", "density", "overlap"} <= names


# -- #74: the hierarchy kept --------------------------------------------------------------


def design() -> EditSession:
    session = EditSession()
    session.nodes.add(RefShape(name="comb", component="mems.comb_drive"))
    session.nodes.add(RefShape(name="tilted", component="mems.comb_drive", x=500, rotation=45))
    session.nodes.add(
        RefShape(
            name="pads",
            component="mems.anchor",
            x=300,
            modifiers=[ArrayModifier(columns=3, rows=2, dx=50, dy=60)],
        )
    )
    return session


def read(path) -> layout.Layout:
    return layout.read(path.read_bytes())


def test_components_are_cells_and_arrays_array_references(tmp_path):
    session = design()
    path = tmp_path / "chip.gds"
    export(session.project, path, "gds", component=session.active)
    written = read(path)
    assert set(written.cells) == {"top", "mems_comb_drive", "mems_anchor"}
    top = written.cells["top"]
    placed = {(p.cell, p.columns, p.rows, p.column_step, p.row_step) for p in top.placements}
    assert ("mems_anchor", 3, 2, (50000, 0), (0, 60000)) in placed  # one array reference
    assert ("mems_comb_drive", 1, 1, (0, 0), (0, 0)) in placed
    assert top.polygons  # the comb turned 45° is flattened into the top cell
    assert written.cells["mems_anchor"].polygons and not written.cells["mems_anchor"].placements


def test_oasis_keeps_the_cells_too(tmp_path):
    session = design()
    path = tmp_path / "chip.oas"
    export(session.project, path, "oasis", component=session.active)
    data = path.read_bytes()  # (OASIS is written, not read)
    assert b"mems_anchor" in data and b"mems_comb_drive" in data


def test_the_flat_export_and_the_top_cells_name(tmp_path):
    session = design()
    path = tmp_path / "flat.gds"
    export(
        session.project,
        path,
        "gds",
        component=session.active,
        options={"keep_hierarchy": False, "top_cell": "CHIP"},
    )
    written = read(path)
    assert list(written.cells) == ["CHIP"] and not written.cells["CHIP"].placements


def test_the_hierarchy_draws_what_the_flat_export_does(tmp_path):
    session = design()
    kept, flat = tmp_path / "kept.gds", tmp_path / "flat.gds"
    export(session.project, kept, "gds", component=session.active)
    export(
        session.project, flat, "gds", component=session.active, options={"keep_hierarchy": False}
    )
    a, b = read(kept), read(flat)
    for gds in {layer for cell in b.cells.values() for layer in cell.polygons}:
        area = Region.from_polygons(a.flat("top", gds)).merged().area()
        expected = Region.from_polygons(b.flat(next(iter(b.cells)), gds)).merged().area()
        assert area == pytest.approx(expected, rel=1e-4)  # rounded per cell: nm apart at most


def test_names_and_arrays():
    assert cell_name("mems.comb_drive") == "mems_comb_drive" and cell_name("die") == "die"
    assert _arrays([(0, 0), (5, 0), (10, 0)]) == [(0, 0, 3, 1, 5, 0)]
    assert _arrays([(0, 0), (5, 0), (0, 7), (5, 7)]) == [(0, 0, 2, 2, 5, 7)]
    assert _arrays([(0, 0), (5, 0), (11, 0)]) == [
        (0, 0, 1, 1, 0, 0),
        (5, 0, 1, 1, 0, 0),
        (11, 0, 1, 1, 0, 0),
    ]
    assert _arrays([(3, 4)]) == [(3, 4, 1, 1, 0, 0)]
