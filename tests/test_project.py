import difflib
import json

import pytest

from mems_sketch import (
    ComponentDef,
    Instance,
    Layer,
    ParamDef,
    Process,
    Project,
    RectShape,
    RefShape,
    Repeat,
    _core,
    load,
    load_library,
    save,
)
from mems_sketch.core.component import to_dbu
from mems_sketch.core.process import layer_rules
from mems_sketch.core.project import new_project
from mems_sketch.engine import Engine, project_data
from mems_sketch.storage.project_files import ProjectFormatError


def area(project: Project, component: str | None = None) -> float:
    region = project.render(component).layers.get("device")
    return 0.0 if region is None else region.area() / to_dbu(1) ** 2


def bar(name="bar", length="10 * w") -> ComponentDef:
    return ComponentDef(
        name=name,
        parameters=[
            ParamDef(name="w", default=2, min=0.5),
            ParamDef(name="length", default=length),
        ],
        shapes=[RectShape(layer="device", x0=0, y0=0, x1="length", y1="w")],
    )


def make_project() -> Project:
    project = Project(
        name="demo",
        process=Process(
            layers={"device": Layer("device", 1, 0)},
            constants={"min_gap": 2, "gap": "1.5 * min_gap"},
            rules={r.name: r for r in layer_rules("device", min_width=1.0, min_space=None)},
        ),
    )
    project.define_component(bar())
    project.set_variable("w_top", 3)
    project.add(Instance("b1", "bar", {"w": "w_top"}))
    return project


def test_top_component_parameters_act_as_variables():
    project = make_project()
    assert project.variables == {"w_top": 3.0}
    assert area(project) == pytest.approx(30 * 3)
    project.set_variable("w_top", 1)
    assert area(project) == pytest.approx(10 * 1)


def test_any_component_renders_with_given_parameters():
    project = make_project()
    assert area(project, "bar") == pytest.approx(20 * 2)
    # Given values are evaluated in the caller's scope (here: process constants only).
    geometry = project.render("bar", {"w": 4, "length": "2 * process.gap"})
    assert geometry.layers["device"].area() / to_dbu(1) ** 2 == pytest.approx(6 * 4)
    # Omitted parameters fall back to their defaults, which may use the given ones.
    geometry = project.render("bar", {"w": 4})
    assert geometry.layers["device"].area() / to_dbu(1) ** 2 == pytest.approx(40 * 4)


def test_process_constants_reach_every_component():
    project = make_project()
    project.define_component(bar("spaced", length="process.gap * 10"))
    assert area(project, "spaced") == pytest.approx(30 * 2)
    project.process.constants["min_gap"] = 4
    assert area(project, "spaced") == pytest.approx(60 * 2)


def test_rename_component_updates_references():
    project = make_project()
    project.rename_component("bar", "beam")
    assert project.find("b1").component == "beam"
    assert area(project) == pytest.approx(90)
    with pytest.raises(ValueError, match="already exists"):
        project.rename_component("beam", "anchor")


def test_used_components_are_kept_and_removing_the_top_leaves_a_library():
    project = make_project()
    with pytest.raises(ValueError, match="still used by: top"):
        project.remove_component("bar")
    project.remove_component("top")
    assert project.top is None and project.is_library
    assert project.default_component() == "bar"
    with pytest.raises(ValueError, match="no top component"):
        project.render()  # a library has no default component to render
    assert project.render("bar").layers


def test_a_library_is_saved_and_loaded_without_a_top_component(tmp_path):
    library = Project(name="lib", top=None)
    library.define_component(bar())
    save(library, tmp_path / "lib")
    assert "top: null" in (tmp_path / "lib" / "project.yaml").read_text()
    loaded = load(tmp_path / "lib")
    assert loaded.top is None and list(loaded.components) == ["bar"]
    assert new_project("x", library=True).top is None


def test_libraries_are_namespaced_and_self_contained(tmp_path):
    lib_project = Project(name="lib")
    lib_project.define_component(bar())
    lib_project.define_component(
        ComponentDef(
            name="pair", shapes=[RefShape(component="bar"), RefShape(component="bar", y=10)]
        )
    )
    save(lib_project, tmp_path / "lib")

    project = make_project()  # has its own, different "bar" locally
    project.define_component(bar(length="100"))
    project.libraries["std"] = load_library("std", tmp_path / "lib")
    project.add(Instance("p", "std.pair", y=50))
    # The library's "pair" uses the library's "bar" (20 long), not the local one (100 long).
    pair = project.render_shape(project.find("p")).layers["device"]
    assert pair.area() / to_dbu(1) ** 2 == pytest.approx(2 * 20 * 2)
    assert "std.pair" in project.component_names()


def test_a_whole_project_can_be_used_as_a_component(tmp_path):
    save(make_project(), tmp_path / "chip")
    wafer = Project(name="wafer")
    wafer.libraries["chip"] = load_library("chip", tmp_path / "chip")
    wafer.add(Instance("dies", "chip.top", {"w_top": 2}, repeat=Repeat(columns=3, dx=100)))
    assert area(wafer) == pytest.approx(3 * 20 * 2)


def test_the_engine_builds_identical_instances_once():
    project = make_project()
    project.add(Instance("many", "bar", {"w": 1}, repeat=Repeat(columns=50, dx=30)))
    engine = Engine()
    project.render(engine=engine)
    # top + one "bar" with w_top + one "bar" with w=1 are built; the other 49 copies are kept.
    assert engine.cached == 3


def test_fingerprints_change_only_with_dependencies():
    project = make_project()
    project.define_component(bar("other", length="5"))
    before = project_fingerprints(project)
    project.components["other"].parameters[0] = ParamDef(name="w", default=9)
    after = project_fingerprints(project)
    assert before["bar"] == after["bar"]
    assert before["other"] != after["other"]
    assert before["top"] == after["top"]  # top does not use "other"
    project.components["bar"].parameters[0] = ParamDef(name="w", default=7)
    assert project_fingerprints(project)["top"] != before["top"]  # top uses "bar"


def project_fingerprints(project: Project) -> dict[str, str]:
    core = _core.Project(json.dumps(project_data(project)))
    return {name: core.fingerprint(name) for name in project.components}


def test_cache_stays_correct_across_edits():
    project = make_project()
    engine = Engine()
    assert project.render(engine=engine).layers["device"].area() / 1e6 == pytest.approx(90)
    project.components["bar"].parameters[1] = ParamDef(name="length", default="20 * w")
    assert project.render(engine=engine).layers["device"].area() / 1e6 == pytest.approx(180)


def test_validate_reports_broken_components():
    project = make_project()
    assert project.validate() == []
    project.components["bar"].shapes[0].x1 = "missing_param"
    problems = project.validate()
    assert any("missing_param" in p for p in problems)


def test_project_folder_layout_and_round_trip(tmp_path):
    project = make_project()
    folder = save(project, tmp_path / "demo")
    assert sorted(p.name for p in (folder / "components").iterdir()) == ["bar", "top"]
    assert (folder / "components" / "bar" / "component.yaml").is_file()
    assert (folder / "processes" / "main" / "process.yaml").is_file()
    manifest = (folder / "project.yaml").read_text()
    assert "components: {top: components/top, bar: components/bar}" in manifest
    assert "process: main\nprocesses: {main: processes/main}" in manifest
    assert load(folder) == project
    assert load(folder / "project.yaml") == project


def test_yaml_is_canonical_and_minimal(tmp_path):
    folder = save(make_project(), tmp_path / "demo")
    text = (folder / "components" / "bar" / "component.yaml").read_text()
    assert text == (
        "parameters:\n"
        "  w: {default: 2, min: 0.5}\n"
        "  length: 10 * w\n"
        "shapes:\n"
        "  rect1: {kind: rect, layer: device, x0: 0, y0: 0, x1: length, y1: w}\n"
    )
    process = (folder / "processes" / "main" / "process.yaml").read_text()
    assert "gds: [1, 0]" in process
    assert "device_min_width: {kind: min_width, layers: [device], value: 1}" in process


def component_files(folder):
    return {p.parent.name: p.read_text() for p in (folder / "components").rglob("component.yaml")}


def test_changing_one_value_changes_one_line(tmp_path):
    project = make_project()
    folder = save(project, tmp_path / "demo")
    before = component_files(folder)
    project.set_variable("w_top", 4)
    save(project, folder)
    after = component_files(folder)
    changed = [name for name in before if before[name] != after[name]]
    assert changed == ["top"]
    diff = [
        line
        for line in difflib.unified_diff(
            before["top"].splitlines(), after["top"].splitlines(), lineterm=""
        )
        if line[:1] in "+-" and line[:3] not in ("+++", "---")
    ]
    assert diff == ["-parameters: {w_top: 3}", "+parameters: {w_top: 4}"]


def test_removed_components_lose_their_files(tmp_path):
    project = make_project()
    project.define_component(bar("spare"))
    folder = save(project, tmp_path / "demo")
    (folder / "components" / "spare" / "notes.txt").write_text("kept")
    project.remove_component("spare")
    save(project, folder)
    assert not (folder / "components" / "spare" / "component.yaml").exists()
    assert (folder / "components" / "spare" / "notes.txt").read_text() == "kept"  # not ours


def test_names_come_from_the_files_not_their_places(tmp_path):
    folder = save(make_project(), tmp_path / "demo")
    (folder / "components" / "bar").rename(folder / "components" / "beam_files")
    manifest = folder / "project.yaml"
    manifest.write_text(
        manifest.read_text().replace("bar: components/bar", "bar: components/beam_files")
    )
    assert set(load(folder).components) == {"top", "bar"}
    (folder / "components" / "stray").mkdir()
    (folder / "components" / "stray" / "component.yaml").write_text("description: lost\n")
    assert load(folder).load_notes == [
        "components/stray/component.yaml is not listed, so it was not loaded"
    ]


def test_load_errors_are_explicit(tmp_path):
    folder = save(make_project(), tmp_path / "demo")
    (folder / "components" / "bar" / "component.yaml").unlink()
    with pytest.raises(ProjectFormatError, match="missing .*component.yaml"):
        load(folder)
    with pytest.raises(ProjectFormatError, match="missing"):
        load(tmp_path / "nowhere")
    manifest = folder / "project.yaml"
    manifest.write_text(manifest.read_text().replace("mems-sketch/2", "mems-sketch/1"))
    with pytest.raises(ProjectFormatError, match="not a mems-sketch/2 project file"):
        load(folder)


def test_unknown_keys_of_a_layer_are_ignored(tmp_path):
    # Etch loss was part of a layer once.
    folder = tmp_path / "old"
    project = Project()
    project.add_layer(Layer("device", 1))
    save(project, folder)
    process = folder / "processes" / "main" / "process.yaml"
    process.write_text(process.read_text().replace("gds: [1, 0]", "gds: [1, 0], undercut: 0.3"))
    assert load(folder).layers["device"] == Layer("device", 1)
