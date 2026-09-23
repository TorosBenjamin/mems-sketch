import pytest

pytest.importorskip("PySide6")

from mems_sketch.core.shapes import BooleanShape, RectShape  # noqa: E402
from mems_sketch.gui.document import DesignDocument  # noqa: E402


def area(doc: DesignDocument, layer: str = "device") -> float:
    region = doc.geometry().layers.get(layer)
    return 0.0 if region is None else region.area() / 1e6


@pytest.fixture
def doc(qapp) -> DesignDocument:
    return DesignDocument()


def test_add_primitive_names_and_selects_path(doc):
    first = doc.add_primitive("rect")
    second = doc.add_primitive("rect")
    assert first == ((0, 0),) and second == ((0, 1),)
    assert [s.name for s in doc.design.shapes] == ["rect1", "rect2"]
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
    doc.set_variable("w", 5.0)
    doc.undo()
    assert "w" not in doc.design.variables
    doc.undo()
    assert doc.design.shapes == []
    assert not doc.can_undo()
    doc.redo()
    doc.redo()
    assert "w" in doc.design.variables and len(doc.design.shapes) == 1


def test_wrap_subtract_and_unwrap(doc):
    a = doc.add_shape(RectShape(layer="device", x0=0, y0=0, x1=10, y1=10))
    b = doc.add_shape(RectShape(layer="device", x0=0, y0=0, x1=5, y1=10))
    path = doc.wrap([b, a], "subtract")  # order of selection does not matter; tree order does
    node = doc.node(path)
    assert isinstance(node, BooleanShape) and node.a[0].name == "rect1"
    assert area(doc) == pytest.approx(50)
    doc.unwrap(path)
    assert [s.name for s in doc.design.shapes] == ["rect1", "rect2"]
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
    assert [s.name for s in doc.design.shapes] == ["group1", "circle1"]
    assert doc.design.shapes[0].children == []


def test_duplicate_gives_fresh_names_to_the_whole_subtree(doc):
    a = doc.add_primitive("rect")
    b = doc.add_primitive("rect")
    op = doc.wrap([a, b], "union")
    copy = doc.duplicate(op)
    names = [s.name for s in doc.design.shapes]
    assert names == ["union1", "union2"]
    inner = [c.name for c in doc.node(copy).a + doc.node(copy).b]
    assert inner == ["rect3", "rect4"]


def test_layers_and_variables(doc):
    name = doc.add_layer()
    layer = doc.design.layers[name]
    doc.set_layer(name, type(layer)("oxide", 9, 0, 0.2, 1.0, None))
    assert "oxide" in doc.design.layers and name not in doc.design.layers
    with pytest.raises(ValueError, match="already exists"):
        doc.set_layer("oxide", type(layer)("device", 9))
    doc.set_variable("a", 1.0)
    doc.rename_variable("a", "b")
    assert doc.design.variables == {"b": 1.0}


def test_view_modes_apply_etch(doc):
    doc.set_layer("device", doc.design.layers["device"].__class__("device", 1, 0, 1.0))
    doc.add_shape(RectShape(layer="device", x0=0, y0=0, x1=10, y1=10))
    assert doc.geometry("etched").layers["device"].area() / 1e6 == pytest.approx(64)
    assert doc.geometry("compensated").layers["device"].area() / 1e6 == pytest.approx(144)


def test_save_open_export(doc, tmp_path):
    doc.add_component("comb_drive")
    doc.save(tmp_path / "a.mems")
    assert not doc.dirty
    other = DesignDocument()
    other.open(tmp_path / "a.mems")
    assert other.design == doc.design
    assert other.export(tmp_path / "a.gds", "compensated").exists()
