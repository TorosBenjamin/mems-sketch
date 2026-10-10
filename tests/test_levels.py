"""Components on levels of the layer stack (requirement CMP-10)."""

import pytest

from mems_sketch import ComponentDef, Layer, LayerMapShape, Project, RectShape, RefShape, load, save
from mems_sketch.core.levels import Stack, is_relative
from mems_sketch.core.process import Level
from mems_sketch.editing import EditSession
from mems_sketch.storage.project_files import ProjectFormatError

STACK = [
    Level("poly0", {"anchor": "anchor0"}),
    Level("poly1", {"anchor": "anchor1"}),
    Level("poly2", {"anchor": "anchor2", "via": "via12"}),
]
LAYERS = ["anchor0", "poly0", "anchor1", "poly1", "anchor2", "via12", "poly2", "metal"]


def stacked(default_level=None) -> Project:
    project = Project(name="stacked")
    for number, name in enumerate(LAYERS, start=1):
        project.add_layer(Layer(name, number))
    project.process.levels = list(STACK)
    project.process.default_level = default_level
    return project


def square(**fields) -> RectShape:
    return RectShape(x0=0, y0=0, x1=10, y1=10, **fields)


def post() -> ComponentDef:
    """A plate on its level and its anchor below it: relative layers only."""
    return ComponentDef(name="post", shapes=[square(), square(layer="level.anchor")])


def top_layers(project: Project) -> set[str]:
    return {name for name, region in project.render().layers.items() if not region.is_empty()}


def test_relative_layers():
    stack = Stack.of(STACK)
    assert stack.layer("level", "poly1") == "poly1"
    assert stack.layer("level+1", "poly1") == "poly2"
    assert stack.layer("level-1", "poly1") == "poly0"
    assert stack.layer("level.anchor", "poly2") == "anchor2"
    assert stack.layer("level+1.via", "poly1") == "via12"
    assert stack.layer("metal", "poly1") == "metal"  # by name
    assert stack.place("level+1", None, "poly0") == "poly1"
    assert stack.place("poly2", None, "poly0") == "poly2"
    assert is_relative("level-2.anchor") and not is_relative("levels")
    with pytest.raises(ValueError, match="below the bottom"):
        stack.layer("level-1", "poly0")
    with pytest.raises(ValueError, match="above the top"):
        stack.place("level+1", None, "poly2")
    with pytest.raises(ValueError, match="no 'via' layer"):
        stack.layer("level.via", "poly1")
    with pytest.raises(ValueError, match="not on a role"):
        stack.place("level.anchor", None, "poly1")
    with pytest.raises(ValueError, match="not a level"):
        stack.place("metal", None, "poly1")
    with pytest.raises(ValueError, match="has none"):
        Stack().layer("level", None)


def test_a_component_is_on_its_placement_its_default_or_its_parents_level():
    project = stacked()
    project.define_component(post())
    project.define_component(ComponentDef(name="high", level="poly2", shapes=[square()]))
    project.define_component(
        ComponentDef(
            name="pair",
            shapes=[RefShape(component="post"), RefShape(component="post", level="level+1")],
        )
    )
    project.top_component.shapes = [RefShape(component="pair")]
    assert top_layers(project) == {"poly0", "anchor0", "poly1", "anchor1"}  # the first level
    project.process.default_level = "poly1"
    assert top_layers(project) == {"poly1", "anchor1", "poly2", "anchor2"}
    project.top_component.shapes = [RefShape(component="pair", level="poly2")]
    with pytest.raises(ValueError, match="above the top"):  # its second post
        project.render()
    project.top_component.shapes = [RefShape(component="high")]  # its own default wins
    assert top_layers(project) == {"poly2"}
    project.top_component.shapes = [RefShape(component="high", level="level-1")]
    assert top_layers(project) == {"poly0"}  # ... unless placed elsewhere


def test_the_same_component_on_two_levels_is_built_for_each():
    project = stacked(default_level="poly1")
    project.define_component(post())
    project.top_component.shapes = [
        RefShape(component="post", level="poly1"),
        RefShape(component="post", level="poly2", x=20),
    ]
    layers = project.render().layers
    assert layers["poly1"].area() == layers["poly2"].area() == layers["anchor2"].area()


def test_layer_map_sides_may_be_relative():
    project = stacked(default_level="poly2")
    project.top_component.shapes = [
        LayerMapShape(children=[square()], mapping={"level": "level-1.anchor"})
    ]
    assert top_layers(project) == {"anchor1"}


def test_relative_layers_need_a_stack():
    project = Project(name="flat")
    project.add_layer(Layer("device", 1))
    project.top_component.shapes = [square()]
    with pytest.raises(ValueError, match="layer stack"):
        project.render()
    project.top_component.shapes = [square(layer="device")]  # layers by name still work
    assert top_layers(project) == {"device"}


def test_the_stack_is_saved_and_checked_on_load(tmp_path):
    project = stacked(default_level="poly1")
    save(project, tmp_path / "p")
    loaded = load(tmp_path / "p")
    assert loaded.process.levels == STACK
    assert loaded.process.default_level == "poly1"
    text = (tmp_path / "p" / "process.yaml").read_text()
    (tmp_path / "p" / "process.yaml").write_text(text.replace("via12\n", "nothing\n", 1))
    with pytest.raises(ProjectFormatError, match="'nothing'"):
        load(tmp_path / "p")


def test_the_stack_follows_renamed_and_removed_layers():
    session = EditSession(stacked(default_level="poly1"))
    session.process.set_layer("poly1", Layer("p1", 3))
    assert session.project.process.levels[1].layer == "p1"
    assert session.project.process.default_level == "p1"
    session.process.remove_layer("anchor2")
    assert session.project.process.levels[2].roles == {"via": "via12"}
    session.process.remove_layer("p1")
    assert [lv.layer for lv in session.project.process.levels] == ["poly0", "poly2"]
    assert session.project.process.default_level is None
    with pytest.raises(ValueError, match="does not exist"):
        session.process.set_levels([Level("nothing")])
    with pytest.raises(ValueError, match="twice"):
        session.process.set_levels([Level("poly0"), Level("poly2", {"anchor": "poly0"})])
