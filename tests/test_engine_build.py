"""Components built by the C++ engine against what the Python backend built
(core-architecture.md, step 6): the same geometry on every layer, within 1 nm,
and within the chord tolerance (5 nm) where there are curves: the Python
backend drew circles as segments, the engine keeps them exact until output.

The Python backend is gone; what it built for every case here was recorded
(``data/python_backend.json.gz``) while it was there, keyed by the case's
components and parameters. A case not in the recording cannot be added here:
give it its own expected values instead. Skipped when the engine is not
built, unless MGEOM_REQUIRED is set (CI)."""

import gzip
import hashlib
import json
import os
import random
from pathlib import Path

import pytest

from mems_sketch import (
    Align,
    BooleanShape,
    CircleShape,
    ComponentDef,
    ParamDef,
    PolygonShape,
    RectShape,
    RefShape,
    TransformShape,
)
from mems_sketch.core.process import Layer, Level
from mems_sketch.core.project import Project, new_project
from mems_sketch.core.region import Region
from mems_sketch.core.shapes import (
    ArrayModifier,
    Corner,
    CornersModifier,
    MirrorModifier,
    PolarArrayModifier,
)
from mems_sketch.core.shapes.kinds.arc import ArcShape
from mems_sketch.core.shapes.kinds.fillet import FilletShape
from mems_sketch.core.shapes.kinds.guide import GuideShape
from mems_sketch.core.shapes.kinds.layer_map import LayerMapShape
from mems_sketch.core.shapes.kinds.offset import OffsetShape
from mems_sketch.core.shapes.kinds.path import PathShape
from mems_sketch.core.user_component import PointDef
from mems_sketch.engine import project_data
from mems_sketch.storage import load

try:
    from mems_sketch import _core
except ImportError:
    try:
        import _core  # built in place: PYTHONPATH=build/engine
    except ImportError:
        if os.environ.get("MGEOM_REQUIRED"):
            raise
        pytest.skip("the engine (mems_sketch._core) is not built", allow_module_level=True)

EXAMPLES = Path(__file__).parent.parent / "examples"
STRAIGHT_NM, CURVED_NM = 1, 3  # sliver half-widths: 2 nm, and 6 nm (chord 5 nm + rounding)
# Curves the Python backend has made from segments before an operation (an
# offset of a circle, KLayout's rounded corners): its 5 nm grow with them.
SEGMENTED_NM = 6


RECORDING = Path(__file__).parent / "data" / "python_backend.json.gz"
_recorded: dict[str, object] | None = None


def case_key(project: Project, component: str, params=None, what: str = "geometry") -> str:
    """Everything a case's result depends on, hashed."""

    def dump(components):
        return {n: d.model_dump(mode="json") for n, d in sorted(components.items())}

    data = {
        "components": dump(project.components),
        "libraries": {n: dump(lib.components) for n, lib in sorted(project.libraries.items())},
        "constants": project.process.constants,
        "levels": [(lv.layer, lv.roles) for lv in project.process.levels],
        "default_level": project.process.default_level,
        "component": component,
        "params": params or {},
        "what": what,
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def recorded(key: str):
    global _recorded
    if _recorded is None:
        _recorded = json.loads(gzip.decompress(RECORDING.read_bytes()))
    if key not in _recorded:
        pytest.fail("no recorded Python result for this case: give it expected values of its own")
    return _recorded[key]


def engine_regions(project: Project, component: str, params=None) -> dict[str, Region]:
    core = _core.Project(json.dumps(project_data(project)))
    return {
        layer: Region.from_polygons(polygons, merged=True)
        for layer, polygons in core.build(component, params or {}).items()
    }


def python_regions(project: Project, component: str, params=None) -> dict[str, Region]:
    """What the Python backend built (raises ValueError where it refused)."""
    result = recorded(case_key(project, component, params))
    if result is None:
        raise ValueError("the Python backend refused it")
    return {
        layer: Region.from_polygons(polygons, merged=True) for layer, polygons in result.items()
    }


def python_points(project: Project, component: str) -> dict[str, tuple[float, float]]:
    return {n: tuple(p) for n, p in recorded(case_key(project, component, {}, "points")).items()}


def assert_same(project: Project, component: str, params=None, tolerance=CURVED_NM):
    """Both backends give the same geometry, or both refuse it."""
    try:
        python = python_regions(project, component, params)
    except Exception as python_error:  # noqa: BLE001
        with pytest.raises(ValueError):
            engine_regions(project, component, params)
        return f"both fail: {python_error}"
    engine = engine_regions(project, component, params)
    assert sorted(engine) == sorted(python), (component, sorted(engine), sorted(python))
    for layer in python:
        # A nm more: shrinking rounds to the grid, which can leave a speck.
        difference = (python[layer] ^ engine[layer]).sized(-tolerance - 1)
        assert difference.is_empty(), (component, layer, difference.bbox(), difference.area())
        # The area differs by at most a sliver that wide along every edge.
        allowance = 2 * tolerance * python[layer].perimeter() + 50
        assert abs(engine[layer].area() - python[layer].area()) <= allowance, (component, layer)
    return "same"


def all_components(project: Project) -> list[str]:
    names = [*project.components]
    names += [
        f"{lib}.{n}" for lib, library in project.libraries.items() for n in library.components
    ]
    return names


@pytest.mark.parametrize("folder", ["resonator", "libraries/mems_std"])
def test_the_examples(folder):
    """Every example component the engine builds matches; the others say why not."""
    project = load(EXAMPLES / folder)
    for name in all_components(project):
        assert assert_same(project, name) == "same", name


BUILT_IN_CASES = [  # (component, parameters, built: else both refuse)
    ("anchor", {}, True),
    ("anchor", {"size": 10, "enclosure": 4.9}, True),
    ("anchor", {"size": 10, "enclosure": 5}, False),  # an exclusive limit
    ("comb_drive", {}, True),
    (
        "comb_drive",
        {"fingers": 7, "finger_width": 1.5, "gap": 0.7, "finger_length": 13, "overlap": 5},
        True,
    ),
    ("comb_drive", {"overlap": 40}, False),
    ("serpentine_spring", {}, True),
    ("serpentine_spring", {"turns": 5, "beam_width": 2.2, "span": 31, "pitch": 7.3}, True),
    ("serpentine_spring", {"pitch": 3}, False),  # not more than the beam width
]


@pytest.mark.parametrize("name, params, built", BUILT_IN_CASES)
def test_built_in_components(name, params, built):
    result = assert_same(new_project("built-ins"), name, params, tolerance=STRAIGHT_NM)
    assert (result == "same") == built, result


def rect(layer="device", **corners):
    return RectShape(layer=layer, **corners)


def hand_written() -> Project:
    project = new_project("cases")
    project.process.constants = {"gap": 2.0, "pitch": "3 * gap"}
    project.components.update(
        {
            "plate": ComponentDef(
                name="plate",
                parameters=[ParamDef(name="size", default=60), ParamDef(name="hole", default=1.5)],
                shapes=[
                    BooleanShape(
                        op="subtract",
                        a=[rect(x0="-size/2", y0="-size/2", x1="size/2", y1="size/2")],
                        b=[
                            CircleShape(
                                layer="device",
                                x="-size/2 + process.pitch",
                                y="-size/2 + process.pitch",
                                radius="hole",
                                modifiers=[
                                    ArrayModifier(
                                        columns="floor(size / process.pitch) - 1",
                                        rows="floor(size / process.pitch) - 1",
                                        dx="process.pitch",
                                        dy="process.pitch",
                                    )
                                ],
                            )
                        ],
                    )
                ],
            ),
            "comb": ComponentDef(
                name="comb",
                parameters=[ParamDef(name="fingers", default=5, integer=True, min=1)],
                shapes=[
                    rect(x0=0, y0=0, x1="fingers * 8", y1=6),
                    rect(
                        x0="2 + 8 * i",
                        y0=6,
                        x1="6 + 8 * i",
                        y1="30 + 2 * i",  # each finger longer than the last
                        modifiers=[ArrayModifier(columns="fingers")],
                    ),
                ],
            ),
            "shapes": ComponentDef(
                name="shapes",
                shapes=[
                    PolygonShape(
                        layer="metal", points=[(0, 0), (10, 0), (10, 10), (5, 4), (0, 10)]
                    ),
                    CircleShape(layer="metal", x=30, y=0, radius=4, segments=6),  # at least 8
                    CircleShape(layer="metal", x=50, y=0, radius=0.004),  # small: 8 segments
                    CircleShape(layer="metal", x=70, y=0, radius=5, segments=12),
                    BooleanShape(
                        op="xor",
                        a=[rect(layer="metal", x0=0, y0=20, x1=20, y1=30)],
                        b=[CircleShape(layer="metal", x=10, y=25, radius=7)],
                    ),
                    BooleanShape(
                        op="intersect",
                        a=[rect(x0=0, y0=40, x1=20, y1=60)],
                        b=[
                            rect(x0=10, y0=50, x1=30, y1=70),
                            rect(layer="metal", x0=0, y0=0, x1=1, y1=1),
                        ],
                    ),
                    TransformShape(
                        x=100,
                        y=10,
                        rotation=30,
                        mirror_x=True,
                        scale=2,
                        children=[rect(x0=0, y0=0, x1=10, y1=3)],
                    ),
                    rect(x0=0, y0=0, x1=0, y1=10),  # no area: nothing
                    rect(layer="anchor", x0=-5, y0=-5, x1=0, y1=0, enabled=False),
                ],
            ),
            # The Python backend scales a circle's segments, and their 5 nm with them.
            "scaled_circle": ComponentDef(
                name="scaled_circle",
                shapes=[TransformShape(scale=2, children=[CircleShape(layer="device", radius=2)])],
            ),
            "chip": ComponentDef(
                name="chip",
                parameters=[ParamDef(name="n", default=3)],
                shapes=[
                    RefShape(component="plate", params={"size": "20 * n"}),
                    RefShape(component="comb", x=100, rotation=90, params={"fingers": "n + 1"}),
                    RefShape(component="comb", x=200, mirror_x=True),
                    TransformShape(
                        x=-100,
                        rotation=45,
                        children=[RefShape(component="shapes", x=5, y=5)],
                        modifiers=[ArrayModifier(rows=2, dy=200)],
                    ),
                ],
            ),
        }
    )
    project.components["top"] = ComponentDef(name="top", shapes=[RefShape(component="chip")])
    return project


@pytest.mark.parametrize("component", ["plate", "shapes", "chip", "top"])
def test_hand_written_components(component):
    assert assert_same(hand_written(), component) == "same"


def test_straight_edges_within_1_nm():
    assert assert_same(hand_written(), "comb", tolerance=STRAIGHT_NM) == "same"


def test_a_scaled_circle():
    assert assert_same(hand_written(), "scaled_circle", tolerance=2 * CURVED_NM) == "same"


@pytest.mark.parametrize(
    "component, params",
    [
        ("plate", {"size": 40, "hole": 2}),
        ("plate", {"size": "10 * process.gap"}),
        ("comb", {"fingers": 1}),
        ("comb", {"fingers": 0}),  # below its minimum: both refuse
        ("comb", {"fingers": 2.5}),  # not an integer
        ("chip", {"n": 2}),
    ],
)
def test_parameter_values(component, params):
    assert_same(hand_written(), component, params)


def test_errors_alike():
    project = hand_written()
    project.components["bad"] = ComponentDef(
        name="bad",
        shapes=[rect(x0=0, y0=0, x1="1 / 0", y1=1)],
    )
    project.components["negative"] = ComponentDef(
        name="negative",
        shapes=[rect(x0=0, y0=0, x1=1, y1=1, modifiers=[ArrayModifier(columns=-1)])],
    )
    project.components["internal"] = ComponentDef(
        name="internal",
        parameters=[ParamDef(name="k", default=1, internal=True)],
        shapes=[rect(x0=0, y0=0, x1="k", y1=1)],
    )
    project.components["places_internal"] = ComponentDef(
        name="places_internal", shapes=[RefShape(component="internal", params={"k": 2})]
    )
    project.components["scaled"] = ComponentDef(
        name="scaled", shapes=[TransformShape(scale=0, children=[rect(x0=0, y0=0, x1=1, y1=1)])]
    )
    for name in ("bad", "negative", "places_internal", "scaled"):
        assert assert_same(project, name).startswith("both fail"), name


def random_shape(rng: random.Random, depth: int = 0):
    layer = rng.choice(["device", "device", "metal"])
    roll = rng.random()
    if depth < 2 and roll < 0.25:
        return BooleanShape(
            op=rng.choice(["subtract", "intersect", "xor"]),
            a=[random_shape(rng, depth + 1) for _ in range(rng.randint(1, 2))],
            b=[random_shape(rng, depth + 1) for _ in range(rng.randint(1, 3))],
        )
    if depth < 2 and roll < 0.35:
        return TransformShape(
            x=rng.uniform(-20, 20),
            y=rng.uniform(-20, 20),
            rotation=rng.choice([0, 90, 180, 37.5]),
            mirror_x=rng.random() < 0.3,
            children=[random_shape(rng, depth + 1) for _ in range(rng.randint(1, 2))],
        )
    modifiers = []
    if rng.random() < 0.2:
        modifiers = [
            ArrayModifier(
                columns=rng.randint(1, 4), rows=rng.randint(1, 3), dx=rng.uniform(3, 12), dy=7
            )
        ]
    x, y = round(rng.uniform(-30, 30), 3), round(rng.uniform(-30, 30), 3)
    if roll < 0.65:
        w, h = round(rng.uniform(0.5, 25), 3), round(rng.uniform(0.5, 25), 3)
        return rect(layer=layer, x0=x, y0=y, x1=x + w, y1=y + h, modifiers=modifiers)
    if roll < 0.8:
        points = [
            (round(x + rng.uniform(-10, 10), 3), round(y + rng.uniform(-10, 10), 3))
            for _ in range(3)
        ]
        return PolygonShape(layer=layer, points=points, modifiers=modifiers)
    return CircleShape(
        layer=layer, x=x, y=y, radius=round(rng.uniform(0.5, 12), 3), modifiers=modifiers
    )


def test_random_shape_trees():
    rng = random.Random(20261010)
    project = new_project("random")
    names = []
    for k in range(40):
        name = f"tree{k}"
        project.components[name] = ComponentDef(
            name=name, shapes=[random_shape(rng) for _ in range(rng.randint(1, 4))]
        )
        names.append(name)
    for name in names:
        assert_same(project, name)


# -- alignment and points -------------------------------------------------------


def aligned() -> Project:
    project = new_project("aligned")
    project.components.update(
        {
            "post": ComponentDef(
                name="post",
                parameters=[ParamDef(name="h", default=12)],
                shapes=[
                    rect(name="stem", x0=0, y0=0, x1=4, y1="h"),
                    CircleShape(
                        name="cap",
                        layer="metal",
                        radius=3,
                        align=Align(point="bottom", to="stem.top"),
                    ),
                ],
                points=[
                    PointDef(name="tip", at="cap.top"),
                    PointDef(name="foot", x="stem.left.x", y="stem.bottom.y - 1"),
                    PointDef(name="corner", at="top_right", x=1, y=-1),
                    PointDef(name="origin"),
                ],
            ),
            "frame": ComponentDef(
                name="frame",
                shapes=[
                    rect(name="base", x0=0, y0=0, x1=60, y1=8),
                    # aligned before it is drawn in the list: evaluated after what it needs
                    rect(
                        name="left",
                        x0=0,
                        y0=0,
                        x1=6,
                        y1=20,
                        align=Align(point="bottom_left", to="base.top_left", dy=1),
                    ),
                    rect(
                        name="beam",
                        x0="left.right.x",
                        y0="left.top.y - 4",
                        x1="right.left.x",
                        y1="left.top.y",
                    ),
                    rect(
                        name="right",
                        x0=0,
                        y0=0,
                        x1=6,
                        y1=20,
                        align=Align(point="bottom_right", to="base.top_right", dy=1),
                    ),
                    RefShape(
                        name="p1", component="post", align=Align(point="foot", to="base.center")
                    ),
                    RefShape(
                        name="p2",
                        component="post",
                        rotation=90,
                        params={"h": 6},
                        align=Align(point="tip", to="p1.tip", dx=10),
                    ),
                    TransformShape(
                        rotation=30,
                        x=100,
                        children=[
                            rect(
                                name="inner",
                                x0=0,
                                y0=0,
                                x1="base.right.x / 10",
                                y1=2,
                                align=Align(point="center", to="base.center"),
                            )
                        ],
                    ),
                    BooleanShape(
                        op="subtract",
                        a=[rect(name="slab", x0=0, y0=-30, x1=40, y1=-20)],
                        b=[
                            CircleShape(
                                name="hole",
                                layer="device",
                                x="slab.center.x",
                                y="slab.center.y",
                                radius=2,
                            )
                        ],
                    ),
                    rect(
                        name="teeth",
                        x0=0,
                        y0=-40,
                        x1=2,
                        y1=-36,
                        modifiers=[
                            ArrayModifier(columns="floor(self.right.x * 10)", dx="self.right.x * 2")
                        ],
                    ),
                ],
                points=[
                    PointDef(name="mid", at="beam.center"),
                    PointDef(name="post_tip", x="p2.tip.x", y="p2.tip.y"),
                ],
            ),
        }
    )
    project.components["top"] = ComponentDef(
        name="top",
        shapes=[
            RefShape(name="f", component="frame", mirror_x=True, x=5),
            rect(name="mark", x0=0, y0=0, x1=1, y1=1, align=Align(point="center", to="f.post_tip")),
        ],
        points=[PointDef(name="mark", at="mark.center")],
    )
    return project


@pytest.mark.parametrize("component", ["post", "frame", "top"])
def test_alignment_and_points(component):
    project = aligned()
    assert assert_same(project, component, tolerance=CURVED_NM) == "same"
    python = python_points(project, component)
    core = _core.Project(json.dumps(project_data(project))).points(component, {})
    assert sorted(core) == sorted(python)
    for name, (x, y) in python.items():
        assert core[name] == pytest.approx((x, y), abs=2e-3), name


def test_point_errors_alike():
    project = aligned()
    project.components["loop"] = ComponentDef(
        name="loop",
        shapes=[
            rect(name="a", x0=0, y0=0, x1=1, y1=1, align=Align(to="b.center")),
            rect(name="b", x0=0, y0=0, x1=1, y1=1, align=Align(to="a.center")),
        ],
    )
    project.components["nowhere"] = ComponentDef(
        name="nowhere",
        shapes=[rect(name="a", x0=0, y0=0, x1=1, y1=1, align=Align(to="ghost.center"))],
    )
    project.components["no_point"] = ComponentDef(
        name="no_point",
        shapes=[rect(name="a", x0=0, y0=0, x1=1, y1=1), rect(x0=0, y0=0, x1="a.middle.x", y1=1)],
    )
    project.components["bad_at"] = ComponentDef(
        name="bad_at",
        shapes=[rect(name="a", x0=0, y0=0, x1=1, y1=1)],
        points=[PointDef(name="p", at="ghost.top")],
    )
    for name in ("loop", "nowhere", "no_point", "bad_at"):
        assert assert_same(project, name).startswith("both fail"), name


# -- the other kinds ----------------------------------------------------------------

CROSS = [rect(x0=0, y0=0, x1=10, y1=5), rect(x0=4, y0=0, x1=6, y1=20)]
TRIANGLE = PolygonShape(layer="device", points=[(0, 0), (20, 0), (10, 5)])
STAR = PolygonShape(
    layer="device",
    points=[(0, 10), (3, 3), (10, 0), (3, -3), (0, -10), (-3, -3), (-10, 0), (-3, 3)],
)
FRAME = BooleanShape(
    op="subtract", a=[rect(x0=0, y0=0, x1=20, y1=20)], b=[rect(x0=5, y0=5, x1=15, y1=15)]
)

# (name, shapes, tolerance in nm)
KIND_CASES = [
    (
        "arc",
        [
            ArcShape(
                layer="device",
                x=1,
                y=2,
                inner_radius=5,
                outer_radius=9,
                start_angle=10,
                end_angle=200,
            )
        ],
        CURVED_NM,
    ),
    ("ring", [ArcShape(layer="device", inner_radius=5, outer_radius=9)], CURVED_NM),
    ("pie", [ArcShape(layer="device", outer_radius=9, start_angle=-30, end_angle=45)], CURVED_NM),
    (
        "arc_segments",
        [ArcShape(layer="device", inner_radius=5, outer_radius=9, end_angle=90, segments=12)],
        STRAIGHT_NM,
    ),
    (
        "path",
        [PathShape(layer="device", points=[(0, 0), (50, 0), (50, 30), (80, 40)], width=4)],
        STRAIGHT_NM,
    ),
    (
        "path_square",
        [PathShape(layer="device", points=[(0, 0), (50, 0), (50, 30)], width=4, ends="square")],
        STRAIGHT_NM,
    ),
    (
        "path_sharp_turn",
        [PathShape(layer="device", points=[(0, 0), (50, 0), (0, 10)], width=4)],
        STRAIGHT_NM,
    ),
    # KLayout draws round ends with its own, coarser segments.
    (
        "path_round",
        [PathShape(layer="device", points=[(0, 0), (50, 0), (50, 30)], width=4, ends="round")],
        10,
    ),
    ("offset", [OffsetShape(distance=2, children=[rect(x0=0, y0=0, x1=10, y1=5)])], STRAIGHT_NM),
    ("offset_shrink", [OffsetShape(distance=-1, children=CROSS)], STRAIGHT_NM),
    (
        "offset_bevel",
        [OffsetShape(distance=2, corners="bevel", children=[rect(x0=0, y0=0, x1=10, y1=5)])],
        STRAIGHT_NM,
    ),
    (
        "offset_shrink_bevel",
        [OffsetShape(distance=-1, corners="bevel", children=CROSS)],
        STRAIGHT_NM,
    ),
    ("offset_sharp_corners", [OffsetShape(distance=1, children=[TRIANGLE])], STRAIGHT_NM),
    ("offset_star", [OffsetShape(distance=0.8, children=[STAR])], STRAIGHT_NM),
    ("offset_star_shrink", [OffsetShape(distance=-0.5, children=[STAR])], STRAIGHT_NM),
    ("offset_holed", [OffsetShape(distance=1, children=[FRAME])], STRAIGHT_NM),
    ("offset_holed_shrink", [OffsetShape(distance=-1.5, children=[FRAME])], STRAIGHT_NM),
    (
        "offset_circle",
        [OffsetShape(distance=1.5, children=[CircleShape(layer="device", radius=4)])],
        SEGMENTED_NM,
    ),
    (
        "offset_pie",
        [
            OffsetShape(
                distance=1,
                children=[ArcShape(layer="device", outer_radius=9, start_angle=-30, end_angle=45)],
            )
        ],
        # Where the arc meets a straight edge, KLayout's miter follows its last
        # segment, a little off the arc's tangent the engine follows.
        20,
    ),
    ("fillet", [FilletShape(radius=2, children=[rect(x0=0, y0=0, x1=10, y1=8)])], SEGMENTED_NM),
    (
        "fillet_inner",
        [
            FilletShape(
                radius=1,
                inner_radius=0.5,
                children=[rect(x0=0, y0=0, x1=10, y1=3), rect(x0=0, y0=0, x1=3, y1=10)],
            )
        ],
        SEGMENTED_NM,
    ),
    (
        "layer_map",
        [
            LayerMapShape(
                mapping={"device": "metal"},
                children=[
                    rect(x0=0, y0=0, x1=3, y1=3),
                    rect(layer="anchor", x0=5, y0=0, x1=6, y1=1),
                ],
            )
        ],
        STRAIGHT_NM,
    ),
    (
        "layer_map_keep",
        [
            LayerMapShape(
                mapping={"device": "metal", "anchor": "metal"},
                keep_unmapped=True,
                children=[
                    rect(x0=0, y0=0, x1=3, y1=3),
                    rect(layer="anchor", x0=2, y0=0, x1=6, y1=1),
                    rect(layer="device_2", x0=0, y0=9, x1=1, y1=10),
                ],
            )
        ],
        STRAIGHT_NM,
    ),
    (
        "guide",
        [
            GuideShape(name="axis", x0=10, y0=0, x1=10, y1=20),
            rect(x0=0, y0=0, x1=2, y1=2, align=Align(point="center", to="axis.center")),
        ],
        STRAIGHT_NM,
    ),
]


@pytest.mark.parametrize("name, shapes, tolerance", KIND_CASES, ids=[c[0] for c in KIND_CASES])
def test_the_other_kinds(name, shapes, tolerance):
    project = new_project("kinds")
    project.components[name] = ComponentDef(name=name, shapes=shapes)
    assert assert_same(project, name, tolerance=tolerance) == "same"


def test_kind_errors_alike():
    project = new_project("kind errors")
    cases = {
        "arc_radii": [ArcShape(layer="device", inner_radius=9, outer_radius=5)],
        "arc_angles": [ArcShape(layer="device", outer_radius=5, start_angle=90, end_angle=10)],
        "path_width": [PathShape(layer="device", points=[(0, 0), (5, 0)], width=0)],
        "guide_point": [GuideShape(x0=1, y0=1, x1=1, y1=1)],
        "fillet_negative": [FilletShape(radius=-1, children=[rect(x0=0, y0=0, x1=4, y1=4)])],
    }
    for name, shapes in cases.items():
        project.components[name] = ComponentDef(name=name, shapes=shapes)
        assert assert_same(project, name).startswith("both fail"), name


# -- the other modifiers ------------------------------------------------------------

BLADE = {"x0": 10, "y0": -1, "x1": 20, "y1": 1}
MODIFIER_CASES = [
    ("polar", [rect(**BLADE, modifiers=[PolarArrayModifier(count=6)])], STRAIGHT_NM),
    (
        "polar_step",
        [rect(**BLADE, modifiers=[PolarArrayModifier(count=3, step=30, x=5, y=5)])],
        STRAIGHT_NM,
    ),
    (
        "polar_upright",
        [rect(**BLADE, modifiers=[PolarArrayModifier(count=5, rotate=False)])],
        STRAIGHT_NM,
    ),
    (
        "polar_index",
        [rect(x0=10, y0=-1, x1="12 + 2 * i", y1=1, modifiers=[PolarArrayModifier(count=4)])],
        STRAIGHT_NM,
    ),
    (
        "mirror_x",
        [
            PolygonShape(
                layer="device",
                points=[(1, 0), (5, 0), (2, 4)],
                modifiers=[MirrorModifier(axis="x", x=-1)],
            )
        ],
        STRAIGHT_NM,
    ),
    (
        "mirror_y",
        [
            PolygonShape(
                layer="device",
                points=[(1, 0), (5, 0), (2, 4)],
                modifiers=[MirrorModifier(axis="y", y=-2, keep=False)],
            )
        ],
        STRAIGHT_NM,
    ),
    (
        "mirror_both",
        [
            PolygonShape(
                layer="device",
                points=[(1, 1), (5, 1), (2, 4)],
                modifiers=[MirrorModifier(axis="both")],
            )
        ],
        STRAIGHT_NM,
    ),
    (
        "mirror_guide",
        [
            GuideShape(name="axis", x0=0, y0=0, x1=10, y1=5),
            PolygonShape(
                layer="device",
                points=[(1, 3), (5, 6), (2, 9)],
                modifiers=[MirrorModifier(about="axis")],
            ),
        ],
        STRAIGHT_NM,
    ),
    (
        "mirror_point",
        [
            rect(name="hub", x0=-1, y0=-1, x1=1, y1=1),
            rect(x0=3, y0=0, x1=6, y1=2, modifiers=[MirrorModifier(about="hub.center")]),
        ],
        STRAIGHT_NM,
    ),
    (
        "mirror_self",
        [
            rect(
                x0=3,
                y0=0,
                x1=6,
                y1=2,
                modifiers=[MirrorModifier(about="self.top_left", keep=False)],
            )
        ],
        STRAIGHT_NM,
    ),
    (
        "mirror_self_x",
        [rect(x0=3, y0=0, x1=6, y1=2, modifiers=[MirrorModifier(axis="x", x="self.left.x")])],
        STRAIGHT_NM,
    ),
    (
        "corners_round",
        [
            rect(
                x0=0,
                y0=0,
                x1=10,
                y1=6,
                modifiers=[
                    CornersModifier(
                        corners=[
                            Corner(at="self.top_right", radius=2),
                            Corner(at="self.bottom_left", radius=1),
                        ]
                    )
                ],
            )
        ],
        SEGMENTED_NM,
    ),
    (
        "corners_chamfer",
        [
            rect(
                x0=0,
                y0=0,
                x1=10,
                y1=6,
                modifiers=[
                    CornersModifier(corners=[Corner(x=10, y=0, radius=1.5, style="chamfer")])
                ],
            )
        ],
        STRAIGHT_NM,
    ),
    (
        "corners_neighbour",
        [
            GuideShape(name="cut", x0=4, y0=2, x1=4, y1=8),  # where the slot's corner is
            BooleanShape(
                op="subtract",
                a=[rect(x0=0, y0=0, x1=10, y1=6)],
                b=[rect(name="slot", x0=4, y0=2, x1=12, y1=8)],
                modifiers=[
                    CornersModifier(
                        corners=[
                            Corner(x="cut.start.x", y="cut.start.y", radius=0.5),
                            Corner(at="self.top_left", radius=1),
                        ]
                    )
                ],
            ),
        ],
        SEGMENTED_NM,
    ),
    (
        "stacked",
        [
            rect(
                x0=10,
                y0=0,
                x1=14,
                y1=3,
                modifiers=[
                    ArrayModifier(columns=2, dx=6),
                    MirrorModifier(axis="y"),
                    PolarArrayModifier(count=3, step=120),
                ],
            )
        ],
        STRAIGHT_NM,
    ),
]


@pytest.mark.parametrize(
    "name, shapes, tolerance", MODIFIER_CASES, ids=[c[0] for c in MODIFIER_CASES]
)
def test_the_other_modifiers(name, shapes, tolerance):
    project = new_project("modifiers")
    project.components[name] = ComponentDef(name=name, shapes=shapes)
    assert assert_same(project, name, tolerance=tolerance) == "same"


def test_modifier_errors_alike():
    project = new_project("modifier errors")
    cases = {
        "polar_count": [rect(**BLADE, modifiers=[PolarArrayModifier(count=2.5)])],
        "mirror_nowhere": [rect(**BLADE, modifiers=[MirrorModifier(about="ghost.center")])],
        "mirror_flat_guide": [
            GuideShape(name="g", x0=1, y0=1, x1=1, y1=1),
            rect(**BLADE, modifiers=[MirrorModifier(about="g")]),
        ],
        "not_a_corner": [
            rect(x0=0, y0=0, x1=10, y1=6, modifiers=[CornersModifier(corners=[Corner(x=5, y=0)])])
        ],
        "too_round": [
            rect(
                x0=0,
                y0=0,
                x1=10,
                y1=6,
                modifiers=[CornersModifier(corners=[Corner(at="self.top_right", radius=7)])],
            )
        ],
        "negative_radius": [
            rect(
                x0=0,
                y0=0,
                x1=10,
                y1=6,
                modifiers=[CornersModifier(corners=[Corner(at="self.top_right", radius=-1)])],
            )
        ],
    }
    for name, shapes in cases.items():
        project.components[name] = ComponentDef(name=name, shapes=shapes)
        assert assert_same(project, name).startswith("both fail"), name


def stacked() -> Project:
    """Components on levels of a layer stack: relative layers, roles, a default
    level, placements on other levels, a relative layer_map."""
    project = new_project("stacked")
    for number, name in enumerate(["anchor0", "poly0", "anchor1", "poly1", "via12", "poly2"]):
        project.add_layer(Layer(name, number + 10))
    project.process.levels = [
        Level("poly0", {"anchor": "anchor0"}),
        Level("poly1", {"anchor": "anchor1"}),
        Level("poly2", {"via": "via12"}),
    ]
    square = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    project.components["post"] = ComponentDef(
        name="post",
        shapes=[RectShape(**square), RectShape(layer="level.anchor", x0=2, y0=2, x1=8, y1=8)],
    )
    project.components["high"] = ComponentDef(
        name="high", level="poly2", shapes=[RectShape(**square), rect(**square)]
    )
    project.components["pair"] = ComponentDef(
        name="pair",
        shapes=[
            RefShape(component="post"),
            RefShape(component="post", level="level+1", x=20),
            RefShape(component="high", x=40),
            RefShape(component="high", level="poly0", x=60),
            LayerMapShape(
                children=[RectShape(x0=80, y0=0, x1=90, y1=10)], mapping={"level": "level+2.via"}
            ),
        ],
    )
    project.components["too_high"] = ComponentDef(
        name="too_high", shapes=[RefShape(component="pair", level="level+2")]
    )
    project.components["no_role"] = ComponentDef(
        name="no_role", level="poly2", shapes=[RectShape(layer="level.anchor", **square)]
    )
    project.components["on_a_role"] = ComponentDef(
        name="on_a_role", shapes=[RefShape(component="post", level="level.anchor")]
    )
    project.top_component.shapes = [RefShape(component="pair")]
    return project


@pytest.mark.parametrize("default", [None, "poly0", "poly1"])
def test_levels(default):
    project = stacked()
    project.process.default_level = default
    for name in ("post", "high", "pair", "top"):
        if default == "poly1" and name in ("pair", "top"):  # its high post: above the top
            assert assert_same(project, name).startswith("both fail"), name
        else:
            assert assert_same(project, name) == "same", name
    for name in ("too_high", "no_role", "on_a_role"):
        assert assert_same(project, name).startswith("both fail"), name
    project.process.levels = []  # the default stack: device (anchor), metal
    assert assert_same(project, "post") == "same"
