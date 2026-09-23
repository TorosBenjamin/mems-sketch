import pytest

from mems_sketch import (
    Align,
    BooleanShape,
    CircleShape,
    ComponentDef,
    Instance,
    ParamDef,
    PointDef,
    Project,
    RectShape,
    Repeat,
    TransformShape,
    load,
    save,
)
from mems_sketch.core.compiler import Compiler
from mems_sketch.core.component import DBU_UM
from mems_sketch.core.shapes import GroupShape


def rect(name, x0, y0, x1, y1, layer="device", **kw) -> RectShape:
    return RectShape(name=name, layer=layer, x0=x0, y0=y0, x1=x1, y1=y1, **kw)


def bbox(project: Project, component: str | None = None, layer: str = "device"):
    box = project.render(component).layers[layer].bbox()
    return tuple(v * DBU_UM for v in (box.left, box.bottom, box.right, box.top))


def boxes(project: Project, component: str | None = None, layer: str = "device"):
    """Bounding boxes of the separate pieces on ``layer``; moved shapes go on ``metal``."""
    region = project.render(component).layers[layer].merged()
    return sorted(
        tuple(v * DBU_UM for v in (p.bbox().left, p.bbox().bottom, p.bbox().right, p.bbox().top))
        for p in region.each()
    )


def moved(project: Project, component: str | None = None):
    return boxes(project, component, "metal")


def metal(name, x0, y0, x1, y1, **kw) -> RectShape:
    return rect(name, x0, y0, x1, y1, layer="metal", **kw)


def test_align_to_bounding_box_points():
    project = Project()
    project.add(rect("base", 0, 0, 100, 20))
    project.add(metal("post", 0, 0, 10, 30, align=Align(point="bottom", to="base.top")))
    assert moved(project) == [(45, 20, 55, 50)]


def test_alignment_follows_parameter_changes():
    project = Project()
    project.set_variable("w", 100)
    project.add(rect("base", 0, 0, "w", 20))
    project.add(
        metal("post", 0, 0, 10, 30, align=Align(point="bottom_left", to="base.top_right", dx=-10))
    )
    assert moved(project) == [(90, 20, 100, 50)]
    project.set_variable("w", 300)
    assert moved(project) == [(290, 20, 300, 50)]


def test_order_in_the_list_does_not_matter():
    project = Project()
    project.top_component.shapes = [
        metal("post", 0, 0, 10, 30, align=Align(point="bottom", to="base.top")),
        rect("base", 0, 0, 100, 20),
    ]
    assert moved(project) == [(45, 20, 55, 50)]


def test_point_coordinates_in_expressions():
    project = Project()
    project.add(rect("base", 0, 0, 100, 20))
    project.add(metal("bar", "base.left.x", 40, "base.right.x", 45))
    assert moved(project) == [(0, 40, 100, 45)]


def test_component_points_are_placed_with_the_reference():
    project = Project()
    project.define_component(
        ComponentDef(
            name="beam",
            parameters=[ParamDef(name="length", default=50)],
            points=[PointDef(name="tip", x="length", y=1)],
            shapes=[rect("body", 0, 0, "length", 2)],
        )
    )
    project.add(Instance("beam", "beam", {"length": 80}, x=10, rotation=90))
    project.add(metal("pad", 0, 0, 20, 20, align=Align(point="bottom", to="beam.tip")))
    # The tip (80, 1) rotated by 90° and moved by 10: (9, 80).
    assert moved(project) == [pytest.approx((-1, 80, 19, 100))]


def test_points_can_be_measured_from_a_shape_and_passed_up():
    project = Project()
    project.define_component(
        ComponentDef(
            name="suspension",
            points=[
                PointDef(name="tip", at="spring.end"),
                PointDef(name="foot", at="spring.start"),
            ],
            shapes=[Instance("spring", "serpentine_spring", {"turns": 2})],
        )
    )
    project.add(Instance("s", "suspension"))
    project.add(Instance("pad", "anchor", {"size": 40}, align=Align(point="bottom", to="s.tip")))
    spring_top = 2 * 2 * 15 + 3
    assert bbox(project, layer="anchor")[1] == pytest.approx(spring_top + 3)


def test_align_a_reference_with_rotation():
    project = Project()
    project.add(rect("mass", -50, -50, 50, 50))
    project.add(
        Instance(
            "comb",
            "comb_drive",
            rotation=180,
            align=Align(point="moving", to="mass.top", dy=-1),
        )
    )
    pieces = boxes(project)
    # The moving comb overlaps the mass by 1 µm and joins it; the fixed comb stays separate.
    assert len(pieces) == 2
    assert pieces[0][1] == pytest.approx(-50)
    assert pieces[1][1] > 50


def test_booleans_see_both_operands():
    project = Project()
    project.add(
        BooleanShape(
            name="plate",
            op="subtract",
            a=[rect("outline", 0, 0, 40, 40)],
            b=[
                CircleShape(name="hole", layer="device", radius=5, align=Align(to="outline.center"))
            ],
        )
    )
    region = project.render().layers["device"]
    assert region.area() * DBU_UM**2 == pytest.approx(1600 - 3.14159 * 25, rel=1e-3)
    assert region.bbox().center().x * DBU_UM == pytest.approx(20)


def test_transforms_see_outer_points_in_their_own_frame():
    project = Project()
    project.add(rect("base", 0, 0, 100, 20))
    project.add(
        TransformShape(
            name="t",
            x=500,
            rotation=90,
            children=[metal("post", 0, 0, 10, 30, align=Align(point="bottom", to="base.top"))],
        )
    )
    # The post's own "bottom" is measured in its own (rotated) frame, where it faces +x;
    # that point still lands exactly on the base's top point (50, 20).
    assert moved(project) == [pytest.approx((20, 15, 50, 25))]


def test_a_transform_can_itself_be_aligned():
    project = Project()
    project.add(rect("base", 0, 0, 100, 20))
    project.add(
        TransformShape(
            name="pair",
            children=[metal("a", 0, 0, 10, 10), metal("b", 20, 0, 30, 10)],
            align=Align(point="bottom_left", to="base.top_left"),
        )
    )
    assert moved(project) == [(0, 20, 10, 30), (20, 20, 30, 30)]


def test_repeated_nodes_align_as_a_whole():
    project = Project()
    project.add(rect("base", 0, 0, 100, 20))
    project.add(
        metal(
            "teeth",
            0,
            0,
            4,
            10,
            repeat=Repeat(columns=5, dx=10),
            align=Align(point="bottom", to="base.top"),
        )
    )
    # The five teeth span 44 µm and are centred on the base as one piece.
    teeth = moved(project)
    assert len(teeth) == 5
    assert (teeth[0][0], teeth[-1][2]) == (28, 72)
    assert {t[1] for t in teeth} == {20}


def test_cycles_and_bad_references_are_reported():
    project = Project()
    project.add(rect("a", 0, 0, 10, 10))
    project.add(rect("b", 0, 0, 10, 10, align=Align(to="a.top")))
    with pytest.raises(ValueError, match="circular alignment"):
        project.replace("a", rect("a", 0, 0, 10, 10, align=Align(to="b.top")))
    with pytest.raises(ValueError, match="no point 'nose'"):
        project.replace("b", rect("b", 0, 0, 10, 10, align=Align(to="a.nose")))
    with pytest.raises(ValueError, match="no shape named 'ghost'"):
        project.add(rect("c", 0, 0, 1, 1, align=Align(to="ghost.top")))
    with pytest.raises(ValueError, match="bounding-box point"):
        PointDef(name="top")


def test_rename_shape_updates_references():
    definition = ComponentDef(
        name="c",
        points=[PointDef(name="p", at="base.top", x="base.right.x - base.left.x")],
        shapes=[
            rect("base", 0, 0, 10, 10),
            rect("post", "base.left.x", 0, 1, 1, align=Align(to="base.top")),
        ],
    )
    definition.rename_shape("base", "floor")
    post = definition.shapes[1]
    assert post.align.to == "floor.top"
    assert post.x0 == "floor.left.x"
    assert definition.points[0].at == "floor.top"
    assert definition.points[0].x == "floor.right.x - floor.left.x"


def test_group_files_still_load_as_transforms():
    node = TransformShape.model_validate({"kind": "group", "children": []})
    assert node.kind == "transform"
    assert GroupShape is TransformShape


def test_points_and_alignment_round_trip(tmp_path):
    project = Project()
    project.define_component(
        ComponentDef(
            name="c",
            points=[PointDef(name="tip", at="body.right", y=2)],
            shapes=[rect("body", 0, 0, 10, 4)],
        )
    )
    project.add(Instance("c1", "c"))
    project.add(metal("pad", 0, 0, 2, 2, align=Align(point="left", to="c1.tip", dx=1)))
    save(project, tmp_path / "p")
    text = (tmp_path / "p" / "components" / "top.yaml").read_text()
    assert "align: {point: left, to: c1.tip, dx: 1}" in text or "to: c1.tip" in text
    again = load(tmp_path / "p")
    assert moved(again) == moved(project) == [(11, 3, 13, 5)]


def test_points_are_cached_with_geometry():
    project = Project()
    project.define_component(
        ComponentDef(name="c", points=[PointDef(name="tip", x=5)], shapes=[rect("b", 0, 0, 5, 1)])
    )
    for n in range(10):
        project.add(Instance(f"c{n}", "c", y=n * 10))
    compiler = Compiler()
    session = compiler.session(project)
    session.render(project.top)
    assert compiler.misses == 2  # top and c, once each
    assert session.component("c").points(session.component("c").Params()) == {"tip": (5, 0)}


def test_example_stays_connected_when_sizes_change():
    from pathlib import Path

    project = load(Path(__file__).parent.parent / "examples" / "resonator")
    for plate, turns in ((160, 3), (240, 3), (160, 6)):
        project.set_variable("plate", plate)
        project.set_parameter("turns", turns, component="suspension")
        pieces = project.render().layers["device"].merged().count()
        # The mass with its springs, anchors and moving combs, plus the two fixed combs.
        assert pieces == 3, (plate, turns)
