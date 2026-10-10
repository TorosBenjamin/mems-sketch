"""Processes shared in libraries, and the project's changes to them (PRJ-8)."""

import pytest

from mems_sketch import Layer, Project, load, load_library, save
from mems_sketch.core.process import Rule, default_process
from mems_sketch.editing import EditSession
from mems_sketch.storage.project_files import ProjectFormatError


def fab_library(folder) -> Project:
    process = default_process()
    process.constants["min_gap"] = 2
    library = Project(name="fab", top=None, process=process, process_name="surface")
    save(library, folder)
    return library


def using(tmp_path) -> Project:
    fab_library(tmp_path / "fab")
    project = Project(name="chip", process_name="fab.surface")
    project.libraries["fab"] = load_library("fab", tmp_path / "fab")
    project.use_process("fab.surface")
    return project


def test_a_library_shares_its_process(tmp_path):
    project = using(tmp_path)
    assert project.base_process is project.libraries["fab"].processes["surface"]
    assert project.process == project.base_process and project.process is not project.base_process
    assert project.processes == {}  # nothing of its own to save
    assert project.process_names() == ["fab.surface"]
    save(project, tmp_path / "chip")
    manifest = (tmp_path / "chip" / "project.yaml").read_text()
    assert "process: fab.surface\n" in manifest and "overrides" not in manifest
    assert not (tmp_path / "chip" / "processes").exists()
    assert load(tmp_path / "chip") == project


def test_changes_are_saved_as_overrides_and_the_rest_follows_the_library(tmp_path):
    project = using(tmp_path)
    project.process.constants["undercut"] = 3
    project.process.rules["device_min_width"].values["value"] = 1.5
    project.process.rules["device_release"].enabled = False
    project.process.add_rule(Rule("metal_space", "min_space", ["metal"], {"value": 5}))
    project.reasons["device_min_width"] = "test structures"
    save(project, tmp_path / "chip")
    manifest = (tmp_path / "chip" / "project.yaml").read_text()
    assert "overrides:\n  constants: {undercut: 3}\n" in manifest
    assert "device_min_width: {value: 1.5, reason: test structures}" in manifest
    assert "device_release: {enabled: false}" in manifest
    assert "metal_space: {kind: min_space, layers: [metal], value: 5}" in manifest
    again = load(tmp_path / "chip")
    assert again == project
    # The library's process changes: the project follows it except where it changed it.
    library = load(tmp_path / "fab")
    library.process.constants["min_gap"] = 2.5
    library.process.rules["device_min_width"].values["value"] = 3
    library.process.rules["device_min_space"].values["value"] = 4
    save(library, tmp_path / "fab")
    again = load(tmp_path / "chip")
    assert again.process.constants == {"undercut": 3, "min_gap": 2.5}
    assert again.process.rules["device_min_width"].values == {"value": 1.5}
    assert again.process.rules["device_min_space"].values == {"value": 4}


def test_what_a_project_cannot_change_in_a_librarys_process(tmp_path):
    session = EditSession(using(tmp_path))
    edits = session.process
    for refused in (
        edits.add_layer,
        lambda: edits.remove_layer("metal"),
        lambda: edits.set_layer("metal", Layer("metal", 9)),
        lambda: edits.set_levels([]),
    ):
        with pytest.raises(ValueError, match="layers belong to process fab.surface"):
            refused()
    with pytest.raises(ValueError, match="turn it off instead"):
        edits.remove_rule("device_min_width")
    with pytest.raises(ValueError, match="belongs to process"):
        edits.remove_constant("min_gap")
    edits.enable_rule("device_release", False)
    edits.set_reason("device_release", "not modelled")
    assert session.project.reasons == {"device_release": "not modelled"}
    edits.reset_rule("device_release")
    assert session.project.process.rules["device_release"].enabled
    assert session.project.reasons == {}


def test_switching_between_processes(tmp_path):
    session = EditSession(using(tmp_path))
    session.project.processes["mine"] = Project().process
    session.process.use("mine")
    assert session.project.process is session.project.processes["mine"]
    session.process.add_layer()  # its own: layers can change
    session.process.use("fab.surface")
    assert session.project.process == session.project.base_process
    with pytest.raises(ValueError, match="no process 'other'"):
        session.process.use("fab.other")
    session.undo()
    assert session.project.process_name == "mine"


def test_overrides_need_what_they_change(tmp_path):
    project = using(tmp_path)
    save(project, tmp_path / "chip")
    manifest = tmp_path / "chip" / "project.yaml"
    text = manifest.read_text()
    manifest.write_text(text + "overrides:\n  rules:\n    nothing: {value: 1}\n")
    with pytest.raises(ProjectFormatError, match="no rule 'nothing'"):
        load(tmp_path / "chip")
    manifest.write_text(text.replace("fab.surface", "fab.other"))
    with pytest.raises(ValueError, match="no process 'other'"):
        load(tmp_path / "chip")


def test_a_document_keeps_the_process_and_its_overrides(tmp_path):
    project = using(tmp_path)
    project.process.constants["undercut"] = 3
    save(project, tmp_path / "chip.json")
    assert load(tmp_path / "chip.json") == project
