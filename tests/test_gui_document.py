import pytest

pytest.importorskip("PySide6")

from mems_sketch.core.process import Layer  # noqa: E402
from mems_sketch.core.shapes import BooleanShape, RectShape, RefShape  # noqa: E402
from mems_sketch.gui.document import ProjectDocument  # noqa: E402
from mems_sketch.storage import load  # noqa: E402


def area(doc: ProjectDocument, mode: str = "drawn") -> float:
    region = doc.geometry(mode).layers.get("device")
    return 0.0 if region is None else region.area() / 1e6


@pytest.fixture
def doc(qapp) -> ProjectDocument:
    return ProjectDocument()


def test_add_primitive_names_and_selects_path(doc):
    first = doc.add_primitive("rect")
    second = doc.add_primitive("rect")
    assert first == ((0, 0),) and second == ((0, 1),)
    assert [s.name for s in doc.shapes] == ["rect1", "rect2"]
    assert area(doc) == pytest.approx(5000)


def test_invalid_edit_rolls_back_and_keeps_history_clean(doc):
    path = doc.add_primitive("circle")
    before = doc.node(path)
    with pytest.raises(Exception):
        doc.replace_node(path, before.model_copy(update={"radius": "unknown_var"}))
    assert doc.node(path) == before
    assert doc.undo_text() == "Add circle1"


def test_undo_redo(doc):
    doc.add_primitive("rect")
    doc.set_parameter("w", 5.0)
    doc.undo()
    assert doc.active_definition.parameters == []
    doc.undo()
    assert doc.shapes == []
    assert not doc.can_undo()
    doc.redo()
    doc.redo()
    assert doc.active_definition.parameters[0].name == "w" and len(doc.shapes) == 1


def test_wrap_subtract_and_unwrap(doc):
    a = doc.add_shape(RectShape(layer="device", x0=0, y0=0, x1=10, y1=10))
    b = doc.add_shape(RectShape(layer="device", x0=0, y0=0, x1=5, y1=10))
    path = doc.wrap([b, a], "subtract")  # tree order decides A and B, not click order
    node = doc.node(path)
    assert isinstance(node, BooleanShape) and node.a[0].name == "rect1"
    assert area(doc) == pytest.approx(50)
    doc.unwrap(path)
    assert [s.name for s in doc.shapes] == ["rect1", "rect2"]
    assert area(doc) == pytest.approx(100)


def test_wrap_requires_siblings_and_enough_operands(doc):
    a = doc.add_primitive("rect")
    b = doc.add_primitive("rect")
    grouped = doc.wrap([a], "group")
    inner = (*grouped, (0, 0))
    with pytest.raises(ValueError, match="siblings"):
        doc.wrap([inner, ((0, 1),)], "union")
    with pytest.raises(ValueError, match="at least two"):
        doc.wrap([b], "subtract")


def test_delete_several_nodes_including_nested(doc):
    a = doc.add_primitive("rect")
    doc.add_primitive("circle")
    doc.add_primitive("polygon")
    group = doc.wrap([a], "group")
    doc.remove_nodes([(*group, (0, 0)), ((0, 2),)])
    assert [s.name for s in doc.shapes] == ["group1", "circle1"]
    assert doc.shapes[0].children == []


def test_duplicate_gives_fresh_names_to_the_whole_subtree(doc):
    a = doc.add_primitive("rect")
    b = doc.add_primitive("rect")
    op = doc.wrap([a, b], "union")
    copy = doc.duplicate(op)
    assert [s.name for s in doc.shapes] == ["union1", "union2"]
    assert [c.name for c in doc.node(copy).a + doc.node(copy).b] == ["rect3", "rect4"]


def test_components_edit_switch_and_place(doc):
    doc.new_component("pad")
    assert doc.active == "pad"
    doc.set_parameter("size", 20.0, min=1)
    doc.add_shape(RectShape(layer="device", x0=0, y0=0, x1="size", y1="size"))
    doc.set_active("top")
    doc.add_component("pad")
    assert area(doc) == pytest.approx(400)
    doc.replace_node(((0, 0),), doc.node(((0, 0),)).model_copy(update={"params": {"size": 5}}))
    assert area(doc) == pytest.approx(25)


def test_editing_a_component_that_breaks_its_users_is_rolled_back(doc):
    doc.new_component("pad")
    doc.set_parameter("size", 20.0)
    doc.add_shape(RectShape(layer="device", x0=0, y0=0, x1="size", y1="size"))
    doc.set_active("top")
    doc.add_shape(RefShape(name="p", component="pad", params={"size": 3}))
    doc.set_active("pad")
    with pytest.raises(ValueError):
        doc.update_parameter("size", min=10)  # top passes size=3
    assert doc.project.components["pad"].parameters[0].min is None


def test_placing_a_component_inside_itself_is_refused(doc):
    doc.new_component("pad")
    with pytest.raises(ValueError, match="circular"):
        doc.add_component("pad")


def test_rename_delete_and_set_top(doc):
    doc.new_component("pad")
    doc.set_active("top")
    doc.add_component("pad")
    doc.rename_component("pad", "bond_pad")
    assert doc.shapes[0].component == "bond_pad"
    with pytest.raises(ValueError, match="still used"):
        doc.delete_component("bond_pad")
    doc.set_top("bond_pad")
    assert doc.project.top == "bond_pad"


def test_make_component_from_selection_keeps_geometry(doc):
    doc.set_parameter("w", 4.0, min=1)
    doc.set_parameter("h", "2 * w")
    doc.set_parameter("unused", 7.0)
    doc.add_shape(RectShape(layer="device", x0=0, y0=0, x1="w", y1="h"))
    doc.add_shape(RectShape(layer="device", x0=10, y0=0, x1=20, y1=5))
    doc.add_shape(RectShape(layer="device", x0=50, y0=0, x1=60, y1=1))
    before = area(doc)
    path = doc.make_component([((0, 0),), ((0, 1),)], "cell")
    assert area(doc) == pytest.approx(before)
    ref = doc.node(path)
    assert ref.component == "cell" and ref.params == {"h": "h", "w": "w"}
    cell = doc.project.components["cell"]
    assert [p.name for p in cell.parameters] == ["w", "h"]
    assert cell.parameters[0].min == 1
    assert len(doc.shapes) == 2  # the reference and the untouched third rectangle
    doc.set_parameter("w", 5.0)
    assert area(doc) == pytest.approx(5 * 10 + 50 + 10)


def test_process_constants_and_layers(doc):
    doc.set_constant("gap", 3.0)
    doc.add_shape(RectShape(layer="device", x0=0, y0=0, x1="10 * process.gap", y1=1))
    assert area(doc) == pytest.approx(30)
    with pytest.raises(ValueError):
        doc.remove_constant("gap")  # still used
    name = doc.add_layer()
    doc.set_layer(name, Layer("oxide", 9, 0, 0.2, 1.0, None))
    assert "oxide" in doc.project.layers and name not in doc.project.layers


def test_view_modes_apply_etch(doc):
    doc.set_layer("device", Layer("device", 1, 0, 1.0))
    doc.add_shape(RectShape(layer="device", x0=0, y0=0, x1=10, y1=10))
    assert area(doc, "etched") == pytest.approx(64)
    assert area(doc, "compensated") == pytest.approx(144)


def test_save_open_export(doc, tmp_path):
    doc.add_component("comb_drive")
    doc.save(tmp_path / "proj")
    assert not doc.dirty and (tmp_path / "proj" / "project.yaml").exists()
    other = ProjectDocument()
    other.open(tmp_path / "proj" / "project.yaml")
    assert other.project == doc.project and other.path == tmp_path / "proj"
    assert other.export(tmp_path / "a.gds", "compensated").exists()
    assert load(tmp_path / "proj") == doc.project
