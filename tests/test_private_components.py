"""Private components: ``comb/finger`` belongs to ``comb`` and is placed only inside it."""

import pytest

from mems_sketch.core.project import Library, Project
from mems_sketch.core.shapes import RectShape, RefShape
from mems_sketch.core.user_component import ComponentDef
from mems_sketch.editing import EditSession
from mems_sketch.storage import load, save


def rect(size=2.0):
    return RectShape(layer="device", x0=0, y0=0, x1=size, y1=size)


def area(project, component=None) -> float:
    region = project.render(component).layers.get("device")
    return 0.0 if region is None else region.area() / 1e6


@pytest.fixture
def project() -> Project:
    """top places comb; comb places its private finger (by its short name)."""
    return Project(
        components={
            "top": ComponentDef(name="top", shapes=[RefShape(name="c", component="comb")]),
            "comb": ComponentDef(
                name="comb",
                shapes=[
                    RefShape(name="f1", component="finger"),
                    RefShape(name="f2", component="finger", x=10),
                ],
            ),
            "comb/finger": ComponentDef(name="comb/finger", shapes=[rect()]),
        }
    )


def test_a_private_component_is_found_from_inside_its_owner(project):
    assert project.qualify("finger", "comb") == "comb/finger"
    assert project.qualify("finger", "comb/finger") == "comb/finger"  # its siblings too
    assert area(project) == pytest.approx(8)


def test_it_cannot_be_placed_outside_its_owner(project):
    with pytest.raises(KeyError):
        project.qualify("finger", "top")
    with pytest.raises(KeyError, match="private to 'comb'"):
        project.qualify("comb/finger", "top")
    project.components["top"].shapes.append(RefShape(name="f", component="comb/finger"))
    with pytest.raises(ValueError, match="private to 'comb'"):
        project.check_references()


def test_but_it_can_be_opened_by_its_path(project):
    assert project.qualify("comb/finger") == "comb/finger"
    assert area(project, "comb/finger") == pytest.approx(4)


def test_inner_names_hide_outer_ones(project):
    project.components["finger"] = ComponentDef(name="finger", shapes=[rect(10)])
    assert project.qualify("finger", "comb") == "comb/finger"
    assert project.qualify("finger", "top") == "finger"
    assert project.reference_name("finger", "comb") == "finger"  # hidden: ...
    assert project.reference_name("comb/finger", "comb") == "finger"


def test_what_can_be_placed_where(project):
    assert "finger" not in project.component_names("top")
    assert "finger" in project.component_names("comb")
    assert "comb" in project.component_names("top")


def test_a_library_exports_only_its_shared_components(project):
    project.libraries["std"] = Library(
        name="std",
        components={
            "plate": ComponentDef(name="plate", shapes=[RefShape(name="h", component="hole")]),
            "plate/hole": ComponentDef(name="plate/hole", shapes=[rect()]),
        },
    )
    assert project.qualify("hole", "std.plate") == "std.plate/hole"
    assert project.qualify("std.plate", "top") == "std.plate"
    with pytest.raises(KeyError, match="private"):
        project.qualify("std.plate/hole", "top")
    assert "std.plate" in project.component_names("top")
    assert not [n for n in project.component_names("top") if "hole" in n]
    project.check_references()


def test_renaming_the_owner_takes_its_private_components_along(project):
    project.rename_component("comb", "drive")
    assert set(project.components) == {"top", "drive", "drive/finger"}
    assert project.components["drive/finger"].name == "drive/finger"
    assert project.components["top"].shapes[0].component == "drive"
    assert project.components["drive"].shapes[0].component == "finger"
    assert area(project) == pytest.approx(8)


def test_renaming_a_private_component(project):
    project.rename_component("comb/finger", "tooth")
    assert "comb/tooth" in project.components
    assert project.components["comb"].shapes[0].component == "tooth"


def test_making_a_component_shared_and_private_again(project):
    project.move_component("comb/finger", "finger")
    assert project.components["comb"].shapes[0].component == "finger"
    assert project.qualify("finger", "top") == "finger"
    project.move_component("finger", "comb/finger")
    assert "comb/finger" in project.components


def test_moving_is_refused_when_a_user_could_no_longer_see_it(project):
    project.components["other"] = ComponentDef(name="other", shapes=[RefShape(component="comb")])
    with pytest.raises(ValueError, match="private to 'top'"):
        project.move_component("comb", "top/comb")  # "other" places comb
    assert (
        "comb" in project.components and project.components["other"].shapes[0].component == "comb"
    )


def test_a_component_is_not_private_to_itself_or_the_top_to_anyone(project):
    with pytest.raises(ValueError, match="itself"):
        project.move_component("comb", "comb/finger/comb")
    with pytest.raises(ValueError, match="top component"):
        project.move_component("top", "comb/top")


def test_deleting_the_owner_deletes_its_private_components(project):
    with pytest.raises(ValueError, match="still used by: top"):
        project.remove_component("comb")
    project.components["top"].shapes.clear()
    project.remove_component("comb")
    assert set(project.components) == {"top"}


def test_a_private_component_needs_its_owner():
    project = Project(components={"a/b": ComponentDef(name="a/b")})
    with pytest.raises(ValueError, match="missing 'a'"):
        project.check_references()


def test_component_paths_are_checked():
    with pytest.raises(ValueError):
        ComponentDef(name="comb/")
    assert ComponentDef(name="comb/finger").owner == "comb"
    assert ComponentDef(name="comb/finger").short_name == "finger"
    assert ComponentDef(name="comb").owner is None


# -- editing and files -------------------------------------------------------------


@pytest.fixture
def doc(project) -> EditSession:
    doc = EditSession()
    doc.project = project
    doc.set_active("top")
    return doc


def test_new_private_component_and_placing_it(doc):
    assert doc.components.new("tooth", owner="comb") == "comb/tooth"
    doc.set_active("comb")
    path = doc.nodes.add_component("comb/tooth")  # its unique name, e.g. from the explorer
    assert doc.node(path).component == "tooth"  # written as comb sees it
    doc.set_active("top")
    with pytest.raises(ValueError, match="private to 'comb'"):
        doc.nodes.add_component("comb/tooth")
    assert "tooth" not in doc.component_names() and "comb" in doc.component_names()


def test_moving_renames_open_tabs_and_undoes(doc):
    renamed = []
    doc.component_renamed.connect(lambda old, new: renamed.append((old, new)))
    doc.set_active("comb/finger")
    doc.components.move("comb", "top")
    assert doc.active == "top/comb/finger"
    assert ("comb", "top/comb") in renamed and ("comb/finger", "top/comb/finger") in renamed
    doc.undo()
    assert "comb/finger" in doc.project.components


def test_duplicating_a_private_component_keeps_its_owner(doc):
    assert doc.components.copy("comb/finger").startswith("comb/finger_copy")


def test_copying_a_component_takes_its_private_ones_along(doc):
    doc.components.copy("comb", "drive")
    assert {"drive", "drive/finger"} <= set(doc.project.components)
    assert doc.project.components["drive"].shapes[0].component == "finger"
    assert doc.project.qualify("finger", "drive") == "drive/finger"


def test_unpacking_a_component_that_places_private_ones_is_refused(doc):
    with pytest.raises(ValueError, match="make that shared first"):
        doc.components.unpack(((0, 0),))


def test_private_components_are_saved_in_their_owners_folder(doc, tmp_path):
    save(doc.project, tmp_path / "p")
    assert (tmp_path / "p" / "components" / "comb" / "finger.yaml").is_file()
    again = load(tmp_path / "p")
    assert set(again.components) == {"top", "comb", "comb/finger"}
    assert again.components["comb/finger"].name == "comb/finger"
    assert area(again) == pytest.approx(8)
    again.move_component("comb/finger", "finger")
    save(again, tmp_path / "p")
    assert not (tmp_path / "p" / "components" / "comb").exists()  # emptied and removed
    assert (tmp_path / "p" / "components" / "finger.yaml").is_file()
