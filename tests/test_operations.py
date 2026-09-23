import json
import math
import sqlite3

import pytest

from mems_sketch import (
    ArcShape,
    BooleanShape,
    CircleShape,
    ComponentDef,
    Project,
    FilletShape,
    GroupShape,
    Instance,
    Layer,
    LayerMapShape,
    OffsetShape,
    ParamDef,
    PathShape,
    PolygonShape,
    RectShape,
    Repeat,
    load,
    save,
)
from mems_sketch.core.component import to_dbu
from mems_sketch.core.shapes import ARC_TOLERANCE_UM
from mems_sketch.process import etch


def area(design: Project, layer: str = "device") -> float:
    region = design.render().layers.get(layer)
    return 0.0 if region is None else region.area() / to_dbu(1) ** 2


def rect(x0, y0, x1, y1, layer="device", **kw) -> RectShape:
    return RectShape(layer=layer, x0=x0, y0=y0, x1=x1, y1=y1, **kw)


@pytest.fixture
def design() -> Project:
    d = Project()
    d.add_layer(Layer("device", 1, undercut=0.5))
    d.add_layer(Layer("anchor", 2))
    d.add_layer(Layer("metal", 3))
    return d


@pytest.mark.parametrize(
    ("op", "expected"),
    [("union", 175), ("subtract", 75), ("intersect", 25), ("xor", 150)],
)
def test_boolean_ops(design, op, expected):
    design.add(BooleanShape(op=op, a=[rect(0, 0, 10, 10)], b=[rect(5, 5, 15, 15)]))
    assert area(design) == pytest.approx(expected)


def test_booleans_act_per_layer(design):
    # Subtracting metal leaves device untouched; the metal-only operand does not leak through.
    design.add(
        BooleanShape(
            op="subtract",
            a=[rect(0, 0, 10, 10), rect(0, 0, 10, 10, layer="anchor")],
            b=[rect(0, 0, 5, 10, layer="anchor"), rect(0, 0, 10, 10, layer="metal")],
        )
    )
    assert area(design, "device") == pytest.approx(100)
    assert area(design, "anchor") == pytest.approx(50)
    assert "metal" not in design.render().layers


def test_cross_layer_boolean_via_layer_map(design):
    # Device minus the anchor footprint grown by 2 µm: a keep-out ring around the anchor.
    design.add(
        BooleanShape(
            op="subtract",
            a=[rect(-20, -20, 20, 20)],
            b=[
                LayerMapShape(
                    mapping={"anchor": "device"},
                    children=[
                        OffsetShape(distance=2, children=[rect(-5, -5, 5, 5, layer="anchor")])
                    ],
                )
            ],
        )
    )
    assert area(design) == pytest.approx(40 * 40 - 14 * 14)


def test_layer_map_keep_unmapped(design):
    shapes = [rect(0, 0, 1, 1), rect(0, 0, 2, 2, layer="metal")]
    design.add(LayerMapShape(name="a", mapping={"device": "anchor"}, children=shapes))
    assert set(design.render().layers) == {"anchor"}
    design.replace(
        "a", LayerMapShape(mapping={"device": "anchor"}, keep_unmapped=True, children=shapes)
    )
    assert set(design.render().layers) == {"anchor", "metal"}


def test_offset_grow_and_shrink(design):
    design.add(OffsetShape(name="o", distance=1, children=[rect(0, 0, 10, 10)]))
    assert area(design) == pytest.approx(12 * 12)
    design.replace("o", OffsetShape(distance=-1, children=[rect(0, 0, 10, 10)]))
    assert area(design) == pytest.approx(8 * 8)
    # Shrinking past half the width removes the feature entirely.
    design.replace("o", OffsetShape(distance=-6, children=[rect(0, 0, 10, 10)]))
    assert area(design) == 0


def test_fillet_rounds_convex_corners(design):
    r = 2.0
    design.add(FilletShape(radius=r, children=[rect(0, 0, 10, 10)]))
    exact = 100 - (4 - math.pi) * r * r
    assert area(design) == pytest.approx(exact, rel=1e-3)


def test_circle_area_within_tolerance(design):
    r = 50.0
    design.add(CircleShape(layer="device", radius=r))
    # Inscribed polygon: error bounded by perimeter x chord deviation.
    assert math.pi * r * r - area(design) < 2 * math.pi * r * ARC_TOLERANCE_UM * 1.5
    assert area(design) < math.pi * r * r


def test_ring_and_sector(design):
    design.add(ArcShape(name="a", layer="device", inner_radius=10, outer_radius=20))
    assert area(design) == pytest.approx(math.pi * (400 - 100), rel=1e-3)
    design.replace(
        "a",
        ArcShape(layer="device", inner_radius=10, outer_radius=20, start_angle=0, end_angle=90),
    )
    assert area(design) == pytest.approx(math.pi * 300 / 4, rel=1e-3)


def test_path_widths_and_ends(design):
    design.add(PathShape(name="p", layer="device", points=[(0, 0), (10, 0), (10, 10)], width=2))
    flush = area(design)
    assert flush == pytest.approx(2 * 20, abs=2)  # corner overlap is counted once
    design.replace("p", PathShape(layer="device", points=[(0, 0), (10, 0)], width=2, ends="square"))
    assert area(design) == pytest.approx(12 * 2)


def test_group_transform_and_scale(design):
    design.add(GroupShape(children=[rect(0, 0, 10, 2)], x=100, rotation=90, scale=2, name="g"))
    box = design.render().layers["device"].bbox()
    assert (box.left, box.bottom, box.right, box.top) == tuple(to_dbu(v) for v in (96, 0, 100, 20))


def test_repeat_on_an_operation_and_disabled_nodes(design):
    hole = rect(1, 1, 3, 3)
    cell = BooleanShape(op="subtract", a=[rect(0, 0, 4, 4)], b=[hole])
    design.add(GroupShape(children=[cell], repeat=Repeat(columns=3, rows=2, dx=4, dy=4)))
    assert area(design) == pytest.approx(6 * (16 - 4))
    design.add(rect(0, 0, 100, 100, name="big", enabled=False))
    assert area(design) == pytest.approx(6 * 12)


def test_parametric_perforated_plate_updates_with_parameters(design):
    design.define_component(
        ComponentDef(
            name="plate",
            parameters=[
                ParamDef(name="size", default=100, min=10),
                ParamDef(name="hole", default=4, min=1),
                ParamDef(name="pitch", default=20, min=2),
                ParamDef(name="corner", default=2, min=0),
            ],
            shapes=[
                FilletShape(
                    radius="corner",
                    children=[
                        BooleanShape(
                            op="subtract",
                            a=[rect(0, 0, "size", "size")],
                            b=[
                                rect(
                                    "(pitch - hole) / 2",
                                    "(pitch - hole) / 2",
                                    "(pitch + hole) / 2",
                                    "(pitch + hole) / 2",
                                    repeat=Repeat(
                                        columns="floor(size / pitch)",
                                        rows="floor(size / pitch)",
                                        dx="pitch",
                                        dy="pitch",
                                    ),
                                )
                            ],
                        )
                    ],
                )
            ],
        )
    )
    design.set_variable("n_pitch", 20)
    design.add(Instance("p", "plate", {"corner": 0, "pitch": "n_pitch"}))
    assert area(design) == pytest.approx(100 * 100 - 25 * 16)
    design.set_variable("n_pitch", 10)
    assert area(design) == pytest.approx(100 * 100 - 100 * 16)


def test_boolean_between_placed_instances(design):
    design.add(
        BooleanShape(
            name="cut",
            op="subtract",
            a=[Instance("pad", "rectangle", {"width": 40, "height": 40})],
            b=[Instance("slot", "rectangle", {"width": 10, "height": 40}, x=15)],
        )
    )
    assert area(design) == pytest.approx(40 * 40 - 10 * 40)  # slot spans x 10..20
    assert design.find("slot").x == 15


def test_etch_applies_after_booleans(design):
    design.add(BooleanShape(op="subtract", a=[rect(0, 0, 20, 20)], b=[rect(5, 5, 15, 15)]))
    etched = etch.etched(design).layers["device"].area() / to_dbu(1) ** 2
    # Undercut 0.5 on every edge: outer shrinks to 19, the hole grows to 11.
    assert etched == pytest.approx(19 * 19 - 11 * 11)


def test_invalid_operations_do_not_change_the_design(design):
    design.add(rect(0, 0, 1, 1, name="r"))
    with pytest.raises(ValueError):
        design.replace("r", ArcShape(layer="device", inner_radius=5, outer_radius=2))
    with pytest.raises(ValueError, match="already exists"):
        design.add(rect(0, 0, 1, 1, name="r"))
    with pytest.raises(ValueError):
        design.add(GroupShape(children=[rect(0, 0, 1, 1)], scale=0))
    assert len(design.shapes) == 1 and design.find("r").kind == "rect"


def test_remove_nested_shape(design):
    design.add(
        BooleanShape(op="subtract", a=[rect(0, 0, 10, 10)], b=[rect(0, 0, 5, 10, name="cut")])
    )
    design.remove("cut")
    assert area(design) == pytest.approx(100)


def test_polygon_with_expressions(design):
    design.set_variable("h", 6)
    design.add(PolygonShape(layer="device", points=[(0, 0), (4, 0), (0, "h")]))
    assert area(design) == pytest.approx(12)


def test_operation_tree_round_trips(design, tmp_path):
    design.add(
        FilletShape(
            radius=1,
            children=[
                BooleanShape(
                    op="xor",
                    a=[CircleShape(layer="device", radius=10)],
                    b=[
                        PathShape(
                            layer="device", points=[(-20, 0), (20, 0)], width=2, ends="round"
                        ),
                        LayerMapShape(
                            mapping={"anchor": "device"},
                            children=[rect(0, 0, 3, 3, layer="anchor")],
                        ),
                    ],
                )
            ],
        )
    )
    save(design, tmp_path / "ops")
    loaded = load(tmp_path / "ops")
    assert loaded == design
    assert area(loaded) == pytest.approx(area(design))


def test_version_2_files_load_as_references(tmp_path):
    path = tmp_path / "old.mems"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE layers (name TEXT PRIMARY KEY, gds_layer INTEGER NOT NULL,
            gds_datatype INTEGER NOT NULL DEFAULT 0, undercut REAL NOT NULL DEFAULT 0,
            min_width REAL, min_space REAL);
        CREATE TABLE variables (name TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE components (position INTEGER NOT NULL, name TEXT PRIMARY KEY,
            definition TEXT NOT NULL);
        CREATE TABLE instances (position INTEGER NOT NULL, name TEXT PRIMARY KEY,
            component TEXT NOT NULL, params TEXT NOT NULL, x TEXT NOT NULL, y TEXT NOT NULL,
            rotation TEXT NOT NULL, mirror_x INTEGER NOT NULL DEFAULT 0);
        INSERT INTO meta VALUES ('schema_version', '2'), ('name', 'old');
        INSERT INTO layers VALUES ('device', 1, 0, 0, NULL, NULL);
        """
    )
    conn.execute(
        "INSERT INTO instances VALUES (0, 'r', 'rectangle', ?, '5', '0', '0', 0)",
        (json.dumps({"width": 10, "height": 2}),),
    )
    conn.commit()
    conn.close()
    loaded = load(path)
    assert loaded.find("r").component == "rectangle"
    assert area(loaded) == pytest.approx(20)
