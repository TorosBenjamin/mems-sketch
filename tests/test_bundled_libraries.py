"""Bundled libraries (#106), categories and tags (#176), and the built-ins as
components of the bundled mems library (#177)."""

import shutil
from pathlib import Path

import pytest

from mems_sketch import ComponentDef, RefShape, load, save
from mems_sketch.core.project import Project, new_project
from mems_sketch.editing import EditSession
from mems_sketch.libraries import BUNDLED_FOLDER, bundled_library, bundled_names
from mems_sketch.storage import read_project, write_project
from mems_sketch.storage.project_files import ProjectFormatError, item_file, manifest_file

EXAMPLES = Path(__file__).parent.parent / "examples"


def same_geometry(a, b) -> bool:
    return (
        a.layer_names() == b.layer_names()
        and a.bbox() == b.bbox()
        and (a.point_count() == b.point_count())
    )


def test_every_project_has_the_bundled_libraries():
    assert bundled_names() == ["mems", "std"]
    project = Project()
    mems = project.libraries["mems"]
    assert mems.bundled == "mems" and mems.version == 3 and mems.description
    assert {"anchor", "comb_drive", "serpentine_spring", "perforated_plate"} <= set(mems.components)
    assert "surface" in mems.processes
    assert not project.render("mems.comb_drive").is_empty()
    std = project.libraries["std"]
    assert std.bundled == "std" and std.version == 2 and not std.processes
    assert {"cross_mark", "vernier", "bond_pad", "die_frame"} <= set(std.components)


def test_each_project_has_its_own_copy():
    one, two = Project(), Project()
    one.libraries["mems"].components.pop("anchor")
    assert "anchor" in two.libraries["mems"].components
    assert "anchor" in bundled_library("mems").components


def test_a_bare_former_built_in_name_says_where_it_is_now():
    project = new_project("p")
    with pytest.raises(KeyError, match="it is 'mems.comb_drive' now"):
        project.qualify("comb_drive", "top")


def test_a_project_saves_them_as_bundled_with_their_version(tmp_path):
    project = new_project("chip")
    project.components["top"].shapes = [RefShape(name="c", component="mems.comb_drive")]
    save(project, tmp_path / "chip")
    manifest = (manifest_file(tmp_path / "chip")).read_text()
    assert "libraries: {mems: bundled@3, std: bundled@2}" in manifest
    again = load(tmp_path / "chip")
    assert again.libraries["mems"].bundled == "mems" and not again.load_notes
    assert same_geometry(again.render("top"), project.render("top"))


def test_one_under_another_name(tmp_path):
    folder = tmp_path / "chip"
    save(new_project("chip"), folder)
    manifest = manifest_file(folder)
    text = manifest.read_text().replace("mems: bundled@3", "m: 'bundled:mems'")
    manifest.write_text(text)
    project = load(folder)
    assert project.libraries["m"].bundled == "mems" and "m.anchor" in project.component_names()
    save(project, folder)
    assert "m: 'bundled:mems@3'" in manifest.read_text()


def test_another_version_is_noted_and_an_unknown_one_refused(tmp_path):
    folder = tmp_path / "chip"
    save(new_project("chip"), folder)
    manifest = manifest_file(folder)
    manifest.write_text(manifest.read_text().replace("mems: bundled@3", "mems: bundled@0"))
    assert any("saved with version 0" in note for note in load(folder).load_notes)
    manifest.write_text(manifest.read_text().replace("mems: bundled@0", "x: 'bundled:nope'"))
    with pytest.raises(ProjectFormatError, match="no bundled library 'nope'"):
        load(folder)


def test_one_file_documents_list_them_too(tmp_path):
    project = new_project("chip")
    project.components["top"].shapes = [RefShape(name="a", component="mems.anchor")]
    path = write_project(project, tmp_path / "chip.yaml")
    assert "mems: bundled@3" in path.read_text()
    assert same_geometry(read_project(path).render("top"), project.render("top"))


def test_a_bundled_library_cannot_be_removed():
    doc = EditSession()
    with pytest.raises(ValueError, match="bundled library"):
        doc.components.remove_library("mems")


OLD_TOP = """\
description: An old design placing built-ins by their bare names
shapes:
  comb: {ref: comb_drive}
  pad: {ref: anchor, x: 100}
"""


def old_project(tmp_path: Path) -> Path:
    """A project saved before the built-ins moved: no libraries, bare names."""
    folder = save(new_project("old"), tmp_path / "old")
    (item_file(folder / "components" / "top", "component.yaml")).write_text(
        "kind: component\n" + OLD_TOP
    )
    manifest = manifest_file(folder)
    manifest.write_text(
        manifest.read_text().replace("libraries: {mems: bundled@3, std: bundled@2}\n", "")
    )
    return folder


def test_old_files_place_the_mems_components_now(tmp_path):
    project = load(old_project(tmp_path))
    top = project.components["top"]
    assert [s.component for s in top.shapes] == ["mems.comb_drive", "mems.anchor"]
    assert any("bundled library 'mems'" in note for note in project.load_notes)
    assert not project.render("top").is_empty()


def test_a_component_of_its_own_with_a_former_built_in_name_is_kept(tmp_path):
    folder = old_project(tmp_path)
    (folder / "components" / "anchor").mkdir()
    (item_file(folder / "components" / "anchor", "component.yaml")).write_text(
        "kind: component\nshapes:\n  r: {kind: rect, layer: device, x0: 0, y0: 0, x1: 1, y1: 1}\n"
    )
    manifest = manifest_file(folder)
    manifest.write_text(manifest.read_text().replace("{top:", "{anchor: components/anchor, top:"))
    project = load(folder)
    assert [s.component for s in project.components["top"].shapes] == [
        "mems.comb_drive",
        "anchor",
    ]


def test_the_example_project_uses_the_bundled_library():
    project = load(EXAMPLES / "resonator")
    assert not project.load_notes
    assert project.process_name == "mems.surface"
    assert [n for n, lib in project.libraries.items() if not lib.bundled] == []


# -- categories and tags ----------------------------------------------------------


def test_folders_are_categories_and_names_do_not_change():
    mems = bundled_library("mems")
    assert mems.components["serpentine_spring"].category == "springs"
    assert mems.components["comb_drive"].category == "electrodes"
    assert mems.category_title("springs") == "Springs"
    assert mems.categories["springs"]["order"] is not None  # from its category.yaml


def test_a_project_keeps_its_components_categories_and_tags(tmp_path):
    project = new_project("chip")
    project.define_component(
        ComponentDef(name="flexure", category="springs/folded", tags=["spring", "flexure"])
    )
    folder = save(project, tmp_path / "chip")
    path = item_file(folder / "components" / "springs" / "folded" / "flexure", "component.yaml")
    assert path.is_file() and "tags: [spring, flexure]" in path.read_text()
    again = load(folder)
    assert again.components["flexure"].category == "springs/folded"
    assert again.components["flexure"].tags == ["spring", "flexure"]
    # Moved to no category: the old folder goes, references stay.
    again.components["flexure"].category = ""
    save(again, folder)
    assert (
        not path.exists()
        and (item_file(folder / "components" / "flexure", "component.yaml")).is_file()
    )
    document = write_project(project, tmp_path / "chip.yaml")
    assert read_project(document).components["flexure"].category == "springs/folded"


def test_a_category_must_be_folder_names():
    with pytest.raises(ValueError, match="not a category"):
        ComponentDef(name="x", category="no spaces")


def test_a_library_folder_can_be_moved_into_categories(tmp_path):
    library = tmp_path / "lib"
    shutil.copytree(BUNDLED_FOLDER / "mems", library)
    from mems_sketch.storage import load_library

    loaded = load_library("lib", library)
    assert loaded.components["anchor"].category == "anchors"
    assert loaded.bundled is None  # a copy in a folder is a library of its own
