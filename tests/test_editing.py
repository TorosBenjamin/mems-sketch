import shutil
from pathlib import Path

import pytest

from mems_sketch.core.process import Layer
from mems_sketch.core.shapes import BooleanShape, RectShape, RefShape
from mems_sketch.editing import EditSession, Event
from mems_sketch.storage import load


def area(doc: EditSession, mode: str = "drawn") -> float:
    region = doc.results.geometry(mode).layers.get("device")
    return 0.0 if region is None else region.area() / 1e6


@pytest.fixture
def doc() -> EditSession:
    return EditSession()


def test_add_primitive_names_and_selects_path(doc):
    first = doc.nodes.add_primitive("rect")
    second = doc.nodes.add_primitive("rect")
    assert first == ((0, 0),) and second == ((0, 1),)
    assert [s.name for s in doc.shapes] == ["rect1", "rect2"]
    assert area(doc) == pytest.approx(5000)


def test_invalid_edit_rolls_back_and_keeps_history_clean(doc):
    path = doc.nodes.add_primitive("circle")
    before = doc.node(path)
    with pytest.raises(ValueError, match="unknown_var"):
        doc.nodes.replace(path, before.model_copy(update={"radius": "unknown_var"}))
    assert doc.node(path) == before
    assert doc.undo_text() == "Add circle1"


def test_undo_redo(doc):
    doc.nodes.add_primitive("rect")
    doc.parameters.set("w", 5.0)
    doc.undo()
    assert doc.active_definition.parameters == []
    doc.undo()
    assert doc.shapes == []
    assert not doc.can_undo()
    doc.redo()
    doc.redo()
    assert doc.active_definition.parameters[0].name == "w" and len(doc.shapes) == 1


def test_wrap_subtract_and_unwrap(doc):
    a = doc.nodes.add(RectShape(layer="device", x0=0, y0=0, x1=10, y1=10))
    b = doc.nodes.add(RectShape(layer="device", x0=0, y0=0, x1=5, y1=10))
    path = doc.nodes.wrap([b, a], "subtract")  # tree order decides A and B, not click order
    node = doc.node(path)
    assert isinstance(node, BooleanShape) and node.a[0].name == "rect1"
    assert area(doc) == pytest.approx(50)
    doc.nodes.unwrap(path)
    assert [s.name for s in doc.shapes] == ["rect1", "rect2"]
    assert area(doc) == pytest.approx(100)


def test_wrap_requires_siblings_and_enough_operands(doc):
    a = doc.nodes.add_primitive("rect")
    b = doc.nodes.add_primitive("rect")
    grouped = doc.nodes.wrap([a], "transform")
    inner = (*grouped, (0, 0))
    with pytest.raises(ValueError, match="siblings"):
        doc.nodes.wrap([inner, ((0, 1),)], "union")
    with pytest.raises(ValueError, match="at least two"):
        doc.nodes.wrap([b], "subtract")


def test_delete_several_nodes_including_nested(doc):
    a = doc.nodes.add_primitive("rect")
    doc.nodes.add_primitive("circle")
    doc.nodes.add_primitive("polygon")
    group = doc.nodes.wrap([a], "transform")
    doc.nodes.remove([(*group, (0, 0)), ((0, 2),)])
    assert [s.name for s in doc.shapes] == ["transform1", "circle1"]
    assert doc.shapes[0].children == []


def test_duplicate_gives_fresh_names_to_the_whole_subtree(doc):
    a = doc.nodes.add_primitive("rect")
    b = doc.nodes.add_primitive("rect")
    op = doc.nodes.wrap([a, b], "union")
    copy = doc.nodes.duplicate(op)
    assert [s.name for s in doc.shapes] == ["union1", "union2"]
    assert [c.name for c in doc.node(copy).a + doc.node(copy).b] == ["rect3", "rect4"]


def test_components_edit_switch_and_place(doc):
    doc.components.new("pad")
    assert doc.active == "pad"
    doc.parameters.set("size", 20.0, min=1)
    doc.nodes.add(RectShape(layer="device", x0=0, y0=0, x1="size", y1="size"))
    doc.set_active("top")
    doc.nodes.add_component("pad")
    assert area(doc) == pytest.approx(400)
    doc.nodes.replace(((0, 0),), doc.node(((0, 0),)).model_copy(update={"params": {"size": 5}}))
    assert area(doc) == pytest.approx(25)


def test_editing_a_component_that_breaks_its_users_is_rolled_back(doc):
    doc.components.new("pad")
    doc.parameters.set("size", 20.0)
    doc.nodes.add(RectShape(layer="device", x0=0, y0=0, x1="size", y1="size"))
    doc.set_active("top")
    doc.nodes.add(RefShape(name="p", component="pad", params={"size": 3}))
    doc.set_active("pad")
    with pytest.raises(ValueError):
        doc.parameters.update("size", min=10)  # top passes size=3
    assert doc.project.components["pad"].parameters[0].min is None


def test_placing_a_component_inside_itself_is_refused(doc):
    doc.components.new("pad")
    with pytest.raises(ValueError, match="circular"):
        doc.nodes.add_component("pad")


def test_rename_delete_and_set_top(doc):
    doc.components.new("pad")
    doc.set_active("top")
    doc.nodes.add_component("pad")
    doc.components.rename("pad", "bond_pad")
    assert doc.shapes[0].component == "bond_pad"
    with pytest.raises(ValueError, match="still used"):
        doc.components.delete("bond_pad")
    doc.components.set_top("bond_pad")
    assert doc.project.top == "bond_pad"


def test_make_component_from_selection_keeps_geometry(doc):
    doc.parameters.set("w", 4.0, min=1)
    doc.parameters.set("h", "2 * w")
    doc.parameters.set("unused", 7.0)
    doc.nodes.add(RectShape(layer="device", x0=0, y0=0, x1="w", y1="h"))
    doc.nodes.add(RectShape(layer="device", x0=10, y0=0, x1=20, y1=5))
    doc.nodes.add(RectShape(layer="device", x0=50, y0=0, x1=60, y1=1))
    before = area(doc)
    path = doc.components.make([((0, 0),), ((0, 1),)], "cell")
    assert area(doc) == pytest.approx(before)
    ref = doc.node(path)
    assert ref.component == "cell" and ref.params == {"h": "h", "w": "w"}
    cell = doc.project.components["top/cell"]  # private to the component it came from
    assert [p.name for p in cell.parameters] == ["w", "h"]
    assert cell.parameters[0].min == 1
    assert len(doc.shapes) == 2  # the reference and the untouched third rectangle
    doc.parameters.set("w", 5.0)
    assert area(doc) == pytest.approx(5 * 10 + 50 + 10)


def test_process_constants_and_layers(doc):
    doc.process.set_constant("gap", 3.0)
    doc.nodes.add(RectShape(layer="device", x0=0, y0=0, x1="10 * process.gap", y1=1))
    assert area(doc) == pytest.approx(30)
    with pytest.raises(ValueError):
        doc.process.remove_constant("gap")  # still used
    name = doc.process.add_layer()
    doc.process.set_layer(name, Layer("oxide", 9, 0, 0.2, 1.0, None))
    assert "oxide" in doc.project.layers and name not in doc.project.layers


def test_view_modes_apply_etch(doc):
    doc.process.set_layer("device", Layer("device", 1, 0, 1.0))
    doc.nodes.add(RectShape(layer="device", x0=0, y0=0, x1=10, y1=10))
    assert area(doc, "etched") == pytest.approx(64)
    assert area(doc, "compensated") == pytest.approx(144)


def test_save_open_export(doc, tmp_path):
    doc.nodes.add_component("comb_drive")
    doc.save(tmp_path / "proj")
    assert not doc.dirty and (tmp_path / "proj" / "project.yaml").exists()
    other = EditSession()
    other.open(tmp_path / "proj" / "project.yaml")
    assert other.project == doc.project and other.path == tmp_path / "proj"
    assert other.export(tmp_path / "a.gds", "compensated").exists()
    assert load(tmp_path / "proj") == doc.project


# -- alignment, transforms and unpacking -------------------------------------

from mems_sketch.core.shapes import Align, TransformShape
from mems_sketch.core.user_component import ComponentDef, ParamDef, PointDef


def bbox_of(doc: EditSession, path, layer="device"):
    region = doc.results.highlight([path]).layers[layer]
    box = region.bbox()
    return tuple(v / 1000 for v in (box.left, box.bottom, box.right, box.top))


def test_align_and_remove_alignment(doc):
    base = doc.nodes.add_primitive("rect")  # 100 x 50 at the origin
    post = doc.nodes.add(RectShape(name="post", layer="device", x0=0, y0=0, x1=10, y1=10))
    doc.nodes.set_align(post, Align(point="bottom_left", to="rect1.top_right"))
    assert bbox_of(doc, post) == (100, 50, 110, 60)
    targets = [name for name, *_ in doc.results.align_targets(post)]
    assert "rect1.top_right" in targets and not any(t.startswith("post.") for t in targets)
    assert doc.results.scope(post)["rect1.top.y"] == 50
    doc.nodes.set_align(post, None)  # stays where the alignment put it
    assert bbox_of(doc, post) == (100, 50, 110, 60)
    assert doc.node(post).x0 == 100
    assert base == ((0, 0),)


def test_renaming_a_shape_keeps_alignments(doc):
    doc.nodes.add_primitive("rect")
    post = doc.nodes.add(
        RectShape(
            name="post",
            layer="device",
            x0=0,
            y0="rect1.top.y",
            x1=10,
            y1=70,
            align=Align(point="bottom", to="rect1.top"),
        )
    )
    doc.nodes.replace(((0, 0),), doc.node(((0, 0),)).model_copy(update={"name": "base"}))
    node = doc.node(post)
    assert node.align.to == "base.top"
    assert node.y0 == "base.top.y"


def test_invalid_alignment_is_rolled_back(doc):
    a = doc.nodes.add_primitive("rect")
    b = doc.nodes.add_primitive("rect")
    doc.nodes.set_align(b, Align(to="rect1.top"))
    with pytest.raises(ValueError, match="circular"):
        doc.nodes.set_align(a, Align(to="rect2.top"))
    assert doc.node(a).align is None


def test_points_of_the_active_component(doc):
    doc.nodes.add_primitive("rect")
    name = doc.points.add()
    doc.points.update(name, name="tip", at="rect1.right", x=5)
    assert doc.results.declared_points() == {"tip": (105, 25)}
    with pytest.raises(ValueError):
        doc.points.update("tip", at="nothing.here")
    doc.points.remove("tip")
    assert doc.results.declared_points() == {}


def test_transform_becomes_a_component_in_place(doc):
    doc.nodes.add_primitive("rect")
    doc.nodes.add_primitive("circle")
    transform = doc.nodes.wrap([((0, 0),), ((0, 1),)], "transform")
    moved = doc.node(transform).model_copy(update={"x": 500, "rotation": 90})
    doc.nodes.replace(transform, moved)
    before = area(doc)
    box_before = doc.results.geometry().layers["device"].bbox()
    path = doc.components.make([transform], "pair")
    ref = doc.node(path)
    assert isinstance(ref, RefShape) and ref.name == "transform1"
    assert (ref.x, ref.rotation) == (500, 90)
    assert area(doc) == pytest.approx(before)
    assert doc.results.geometry().layers["device"].bbox() == box_before


def test_unpack_restores_the_shapes_with_values_filled_in(doc):
    doc.edit(
        "define",
        lambda p: p.components.__setitem__(
            "bar",
            ComponentDef(
                name="bar",
                parameters=[ParamDef(name="w", default=2), ParamDef(name="l", default="10 * w")],
                points=[PointDef(name="tip", x="l")],
                shapes=[RectShape(name="rect1", layer="device", x0=0, y0=0, x1="l", y1="w")],
            ),
        ),
    )
    doc.parameters.set("width", 3)
    doc.nodes.add_primitive("rect")  # takes the name rect1 in the top component
    ref = doc.nodes.add(RefShape(name="b", component="bar", params={"w": "width"}, x=7))
    before = area(doc)
    doc.components.unpack(ref)
    node = doc.node(ref)
    assert isinstance(node, TransformShape) and node.name == "b" and node.x == 7
    (inner,) = node.children
    assert inner.name == "rect2"  # renamed: rect1 is taken
    assert inner.x1 == "10 * width" and inner.y1 == "width"
    assert area(doc) == pytest.approx(before)
    with pytest.raises(ValueError, match="built-in"):
        doc.components.unpack(doc.nodes.add_component("anchor"))


def test_parameters_can_be_renamed(doc):
    doc.parameters.set("w", 2)
    doc.parameters.update("w", name="width")
    assert [p.name for p in doc.active_definition.parameters] == ["width"]


# -- moving ------------------------------------------------------------------


def test_move_changes_the_right_values_and_is_one_undo_step(doc):
    doc.parameters.set("w", 100)
    rect = doc.nodes.add(RectShape(name="r", layer="device", x0=0, y0=0, x1="w", y1=10))
    doc.moves.move([rect], 5, -2)
    node = doc.node(rect)
    assert (node.x0, node.x1, node.y0, node.y1) == (5, "w + 5", -2, 8)
    doc.undo()
    assert doc.node(rect).x1 == "w"


def test_drag_plan_finds_followers_and_snap_points(doc):
    base = doc.nodes.add(RectShape(name="base", layer="device", x0=0, y0=0, x1=100, y1=20))
    doc.nodes.add(
        RectShape(
            name="post",
            layer="device",
            x0=0,
            y0=0,
            x1=10,
            y1=10,
            align=Align(point="bottom", to="base.top"),
        )
    )
    doc.nodes.add(RectShape(name="bar", layer="device", x0="base.left.x", y0=50, x1=5, y1=55))
    doc.nodes.add(RectShape(name="other", layer="device", x0=200, y0=0, x1=210, y1=10))
    plan = doc.moves.plan_drag([base])
    assert plan.roots == [base] and plan.followers == [((0, 1),)] and plan.loose == [((0, 2),)]
    targets = {name for name, *_ in plan.targets}
    assert "other.top_left" in targets and not any(t.startswith("post.") for t in targets)
    assert {name for _, name, *_ in plan.points} >= {"center", "top_right"}
    assert plan.preview.layers["device"].bbox().top == 30000  # base and post


def test_moving_an_aligned_shape_changes_its_offset_or_detaches_it(doc):
    doc.nodes.add(RectShape(name="base", layer="device", x0=0, y0=0, x1=100, y1=20))
    post = doc.nodes.add(
        RectShape(
            name="post",
            layer="device",
            x0=0,
            y0=0,
            x1=10,
            y1=10,
            align=Align(point="bottom", to="base.top"),
        )
    )
    doc.moves.move([post], 3, 0)
    assert (doc.node(post).align.dx, doc.node(post).x0) == (3, 0)
    doc.moves.move([post], 1, 1, detach=True)
    node = doc.node(post)
    assert node.align is None
    assert (node.x0, node.y0, node.x1, node.y1) == (49, 21, 59, 31)  # stays put, then moves


def test_moving_inside_a_rotated_transform_follows_the_mouse(doc):
    doc.nodes.add(
        TransformShape(
            name="t",
            rotation=90,
            children=[RectShape(name="r", layer="device", x0=0, y0=0, x1=10, y1=10)],
        )
    )
    doc.moves.move([((0, 0), (0, 0))], 5, 0)  # right on screen...
    inner = doc.node(((0, 0), (0, 0)))
    assert (inner.x0, inner.y0) == (0, -5)  # ...is -y in the rotated frame
    assert doc.results.highlight([((0, 0), (0, 0))]).layers["device"].bbox().left == -5000


def test_moving_a_shape_and_its_aligned_partner_moves_both_once(doc):
    base = doc.nodes.add(RectShape(name="base", layer="device", x0=0, y0=0, x1=100, y1=20))
    post = doc.nodes.add(
        RectShape(
            name="post",
            layer="device",
            x0=0,
            y0=0,
            x1=10,
            y1=10,
            align=Align(point="bottom", to="base.top"),
        )
    )
    doc.moves.move([base, post], 10, 0)
    assert doc.node(post).align.dx == 0
    assert doc.results.highlight([post]).layers["device"].bbox().left == 55000


# -- rotating and mirroring --------------------------------------------------


def test_rotating_a_reference_about_a_pivot(doc):
    ref = doc.nodes.add(RefShape(name="a", component="anchor", x=10, y=0))
    doc.moves.rotate([ref], 90, (0, 0))
    node = doc.node(ref)
    assert (node.rotation, node.x, node.y) == pytest.approx((90, 0, 10))
    doc.moves.rotate([ref], 270, (0, 0))
    assert (doc.node(ref).rotation, doc.node(ref).x) == pytest.approx((0, 10))


def test_rotating_a_primitive_wraps_it_and_keeps_its_name(doc):
    rect = doc.nodes.add(RectShape(name="r", layer="device", x0=0, y0=0, x1=20, y1=10))
    post = doc.nodes.add(
        RectShape(
            name="post",
            layer="metal",
            x0=0,
            y0=0,
            x1=2,
            y1=2,
            align=Align(point="bottom", to="r.top"),
        )
    )
    doc.moves.rotate([rect], 90, (0, 0))
    wrapper = doc.node(rect)
    assert isinstance(wrapper, TransformShape) and wrapper.name == "r"
    assert wrapper.children[0].name == "r_shape1"
    box = doc.results.highlight([rect]).layers["device"].bbox()
    assert (box.left, box.bottom, box.right, box.top) == (-10000, 0, 0, 20000)
    # The post stays aligned to the (now rotated) rectangle's top.
    assert doc.results.highlight([post]).layers["metal"].bbox().bottom == 20000


def test_mirroring_and_expressions(doc):
    doc.parameters.set("d", 30)
    ref = doc.nodes.add(RefShape(name="a", component="anchor", x="d", y=0))
    doc.moves.mirror([ref], left_right=True, center=(0, 0))
    node = doc.node(ref)
    assert node.x == "d - 60" and node.mirror_x and node.rotation == 180
    doc.moves.mirror([ref], left_right=True, center=(0, 0))
    node = doc.node(ref)
    assert node.x == "d" and not node.mirror_x and node.rotation == 0


EXAMPLES = Path(__file__).parent.parent / "examples"


def test_events_call_slots_in_order_and_survive_a_failing_one(monkeypatch):
    reported = []
    monkeypatch.setattr("sys.excepthook", lambda *exc: reported.append(exc[0]))
    event, calls = Event(), []

    def fail(*args):
        raise RuntimeError("listener broke")

    event.connect(lambda *a: calls.append(("first", a)))
    event.connect(fail)
    event.connect(lambda *a: calls.append(("last", a)))
    event.emit("old", "new")
    assert calls == [("first", ("old", "new")), ("last", ("old", "new"))]
    assert reported == [RuntimeError]
    event.disconnect(fail)
    event.emit()
    assert reported == [RuntimeError]


def test_a_script_can_edit_and_save_a_project(tmp_path):
    for name in ("resonator", "libraries"):
        shutil.copytree(
            EXAMPLES / name, tmp_path / name, ignore=shutil.ignore_patterns(".mems-sketch")
        )
    session = EditSession.open_project(tmp_path / "resonator")
    session.set_active("suspension")
    before = session.results.geometry()
    session.components.make([((0, 0),), ((0, 1),)], "spring_with_anchor")
    session.save()

    again = EditSession.open_project(tmp_path / "resonator")
    assert "suspension/spring_with_anchor" in again.project.components  # private to suspension
    saved = tmp_path / "resonator" / "components" / "suspension" / "spring_with_anchor.yaml"
    assert saved.read_text().startswith("name: spring_with_anchor\n")
    after = again.results.geometry(component="suspension")
    assert before.layers.keys() == after.layers.keys()
    assert all((before.layers[k] ^ after.layers[k]).is_empty() for k in before.layers)


# -- components: hierarchy, copies, libraries, top ---------------------------------


@pytest.fixture
def resonator(tmp_path) -> EditSession:
    for name in ("resonator", "libraries"):
        shutil.copytree(
            EXAMPLES / name, tmp_path / name, ignore=shutil.ignore_patterns(".mems-sketch")
        )
    return EditSession.open_project(tmp_path / "resonator")


def test_placed_components_form_a_hierarchy(resonator):
    placed = resonator.components.placed
    assert placed("top") == [("std.perforated_plate", 1), ("comb_drive", 2), ("suspension", 2)]
    assert placed("suspension") == [("serpentine_spring", 1), ("anchor", 1)]
    assert placed("comb_drive") == []  # a built-in places nothing
    assert resonator.components.users("suspension") == ["top"]


def test_a_library_component_can_be_copied_into_the_project(resonator):
    name = resonator.components.copy("std.perforated_plate")
    assert name == "perforated_plate" and resonator.active == name
    assert not resonator.read_only
    assert resonator.components.copy("std.perforated_plate") == "perforated_plate_copy1"
    with pytest.raises(ValueError, match="built in"):
        resonator.components.copy("anchor")
    resonator.undo()
    resonator.undo()
    assert "perforated_plate" not in resonator.project.components


def test_libraries_can_be_added_and_removed_with_undo(resonator, tmp_path):
    with pytest.raises(ValueError, match="still used by: top"):
        resonator.components.remove_library("std")
    name = resonator.components.add_library(tmp_path / "libraries" / "mems_std", "extra")
    assert name == "extra" and "extra.perforated_plate" in resonator.project.component_names()
    resonator.components.remove_library("extra")
    assert "extra" not in resonator.project.libraries
    resonator.undo()
    assert "extra" in resonator.project.libraries
    resonator.undo()
    assert "extra" not in resonator.project.libraries
    with pytest.raises(ValueError, match="no components"):
        resonator.components.add_library(tmp_path)


def test_a_project_can_become_a_library_and_back(doc):
    doc.components.new("spring")
    doc.components.set_top(None)
    assert doc.project.is_library
    doc.components.delete("top")
    assert list(doc.project.components) == ["spring"] and doc.active == "spring"
    with pytest.raises(ValueError, match="at least one component"):
        doc.components.delete("spring")
    doc.components.set_top("spring")
    assert doc.project.top == "spring"


def test_a_new_library_has_one_component_and_no_top(doc):
    doc.new(library=True)
    assert doc.project.top is None and doc.active == "component1"
