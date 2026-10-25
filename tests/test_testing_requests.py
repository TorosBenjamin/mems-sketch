"""Requests from usability testing: parameter descriptions and whole numbers in
the Parameters panel, the MCP server's rough edges, libraries outside the
repository, and library parts for released devices (#308–#311)."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QInputDialog, QMessageBox

from mems_sketch.gui.app import MainWindow


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    return w


# -- #308: description and whole number from the Parameters panel ----------------------


def test_a_parameter_is_made_a_whole_number_and_described(window, monkeypatch):
    doc = window.document
    doc.parameters.set("fingers", 10)
    panel = window.parameters
    panel.refresh()
    row = panel._names.index("fingers")
    menu = panel.context_menu(row)
    entries = {a.text(): a for a in menu.actions() if not a.isSeparator()}
    assert {"Description…", "Whole number", "Internal", "Remove"} <= set(entries)
    assert not entries["Whole number"].isChecked()
    entries["Whole number"].trigger()
    assert doc.active_definition.parameters[row].integer
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Moving fingers", True))
    panel.context_menu(row).actions()[0].trigger()  # Description…
    assert doc.active_definition.parameters[row].description == "Moving fingers"
    panel.refresh()
    tip = panel.table.item(row, 0).toolTip()
    assert "Moving fingers" in tip and "A whole number" in tip
    assert doc.undo_text().startswith("Edit parameter")


# -- #311: the MCP server's rough edges -------------------------------------------------


@pytest.fixture
def tools():
    from mems_sketch.core.project import Project
    from mems_sketch.core.user_component import ComponentDef
    from mems_sketch.editing import EditSession
    from mems_sketch.mcp_server.tools import DesignTools

    project = Project(name="t")
    project.components["top"] = ComponentDef(name="top")
    project.top = "top"
    return DesignTools(EditSession(project))


def call(tools, name: str, arguments: dict):
    """One tool call over the protocol: (failed, text)."""
    import anyio
    from mcp.client.client import Client

    from mems_sketch.mcp_server.server import create_server

    async def session():
        async with Client(create_server(tools)) as client:
            result = await client.call_tool(name, arguments)
            return result.is_error, result.content[0].text

    return anyio.run(session)


def test_an_unknown_argument_is_an_error_that_says_what_the_tool_takes(tools):
    """#311: get_component {"name": …} read the top component instead."""
    pytest.importorskip("mcp")
    failed, text = call(tools, "get_component", {"name": "top"})
    assert failed and "no argument 'name'" in text and "it takes component" in text
    assert not call(tools, "get_component", {"component": "top"})[0]


def test_placing_a_component_in_itself_says_which_argument_is_which(tools):
    """#311: the arguments mixed up gave a bare circular-reference error."""
    pytest.importorskip("mcp")
    failed, text = call(tools, "place_component", {"name": "c", "place": "top"})
    assert failed and "`place` is the component to place" in text
    failed, text = call(tools, "place_component", {"name": "a", "place": "mems.anchor"})
    assert not failed and "placed mems.anchor as a in top" in text


def test_limits_take_the_forms_the_parameters_panel_does(tools):
    """#311: set_parameter refused min "> 20", which the GUI takes."""
    pytest.importorskip("mcp")
    failed, _ = call(
        tools, "set_parameter", {"name": "w", "default": 30, "min": "> 20", "max": "< 100"}
    )
    assert not failed
    [w] = tools.session.active_definition.parameters
    assert (w.min, w.min_exclusive, w.max, w.max_exclusive) == (20, True, 100, True)


# -- #310: libraries outside the repository ---------------------------------------------


@pytest.fixture
def outside(git_repo, tmp_path):
    """A saved project in a repository using a library kept outside it: (project
    folder, library folder)."""
    import shutil
    from pathlib import Path

    from mems_sketch.editing import EditSession

    library = tmp_path / "acme"
    shutil.copytree(Path(__file__).parent / "data" / "mems_std", library)
    session = EditSession()
    session.create(git_repo, "demo")
    session.components.add_library(library, "acme")
    session.save()
    return git_repo, library


def test_a_library_outside_the_repository_is_found_and_copied_in(outside):
    from mems_sketch.editing import EditSession
    from mems_sketch.storage import load

    folder, library = outside
    session = EditSession()
    session.open(folder)
    assert session.components.outside_repository() == [("acme", library.resolve())]
    copied = session.components.copy_library_in("acme")
    assert copied == folder / "libraries" / "acme" and (copied / "project.yaml").is_file()
    assert session.components.outside_repository() == []
    session.save()
    assert load(folder).libraries["acme"].path.resolve() == copied.resolve()


def test_a_missing_library_is_named_and_can_be_pointed_at(outside, tmp_path):
    from mems_sketch.storage import load
    from mems_sketch.storage.project_files import MissingLibraryError, relocate_library

    folder, library = outside
    moved = library.rename(tmp_path / "elsewhere")
    with pytest.raises(MissingLibraryError) as missing:
        load(folder)
    assert missing.value.name == "acme" and missing.value.folder == library
    relocate_library(folder, "acme", moved)
    assert load(folder).libraries["acme"].path.resolve() == moved.resolve()


def test_the_window_asks_where_a_missing_library_is(window, outside, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    folder, library = outside
    moved = library.rename(tmp_path / "elsewhere")
    asked = []

    def answer(box):
        asked.append(box.text())
        box.buttons()[0].click()  # Locate library…

    monkeypatch.setattr(QMessageBox, "exec", answer)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(moved))
    assert window.open_project(str(folder))
    assert asked and "'acme'" in asked[0]
    assert "acme" in window.document.project.libraries


def test_the_commit_panel_warns_of_a_library_outside_the_repository(window, outside):
    folder, _ = outside
    window.open_project(str(folder))
    window.tool_windows.open("commit")
    panel = window.commit_panel
    panel.reload()
    assert panel.outside.isVisible() and "'acme'" in panel.outside_note.text()
    panel.copy_in.click()
    assert not panel.outside.isVisible()


# -- #309: library parts for released devices --------------------------------------------


def released(*shapes):
    from mems_sketch.core.project import Project
    from mems_sketch.core.user_component import ComponentDef

    project = Project(name="t")
    project.use_process("mems.surface")
    project.components["top"] = ComponentDef(name="top", shapes=list(shapes))
    project.top = "top"
    return project


def test_the_anchored_bond_pad_stays_put_at_release():
    from mems_sketch import RefShape
    from mems_sketch.process import rules

    project = released(
        RefShape(name="pad", component="mems.bond_pad"),
        RefShape(name="loose", component="std.bond_pad", x=300),
    )
    floating = {v.rule for v in rules.check(project, component="top") if v.bbox_um[0] < 100}
    assert not floating  # mems.bond_pad: anchored and released
    assert rules.check(project, component="top")  # std.bond_pad still floats, as it says


def test_the_folded_flexure_has_a_port_at_its_anchor():
    from mems_sketch import RefShape
    from mems_sketch.editing import EditSession

    session = EditSession(released(RefShape(name="f", component="mems.folded_flexure")))
    port = session.results.all_ports()["f.port.anchor"]
    assert (port.x, port.y) == (0, 0)  # reached on the wiring below (the route's layer)


def test_the_surface_process_has_wiring_under_the_structure():
    project = released()
    levels = [level.layer for level in project.process.levels]
    assert levels == ["wiring", "device", "metal"] and project.process.default_level == "device"
