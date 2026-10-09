"""Rule decks: shared sets of rules with parameters, used by projects that can
set the parameters and override single rules (requirement DRC-8)."""

import shutil
from pathlib import Path

import pytest

from mems_sketch import Layer, RectShape
from mems_sketch.cli import main
from mems_sketch.core.process import Rule, RuleDeck
from mems_sketch.editing import EditSession
from mems_sketch.process.rules import check
from mems_sketch.storage import load, save
from mems_sketch.storage.project_files import load_deck, save_deck


def write_deck(path: Path) -> Path:
    deck = RuleDeck(
        name="fab",
        description="A fab's rules",
        parameters={"min_feature": 3, "space": "min_feature + 1"},
        rules={
            "device_width": Rule("device_width", "min_width", ["device"], {"value": "min_feature"}),
            "device_space": Rule("device_space", "min_space", ["device"], {"value": "space"}),
            "metal_width": Rule(
                "metal_width", "min_width", ["metal"], {"value": "process.undercut"}
            ),
        },
    )
    return save_deck(deck, path)


@pytest.fixture
def doc(tmp_path) -> EditSession:
    session = EditSession()
    for rule in list(session.project.process.rules):
        session.process.remove_rule(rule)  # only the deck's rules
    write_deck(tmp_path / "decks" / "fab.yaml")
    session.process.use_deck(tmp_path / "decks" / "fab.yaml")
    return session


def kinds(session: EditSession) -> list[tuple[str, str]]:
    return sorted((v.rule, v.message) for v in check(session.project))


def test_a_deck_file_reads_back(tmp_path):
    path = write_deck(tmp_path / "fab.yaml")
    text = path.read_text()
    assert text.startswith("format: mems-sketch-rules/1\nname: fab\n")
    assert "value: min_feature" in text
    deck = load_deck(path)
    assert deck.parameters == {"min_feature": 3.0, "space": "min_feature + 1"}
    assert deck.rules["device_width"].values == {"value": "min_feature"}
    with pytest.raises(ValueError, match="no rule deck"):
        load_deck(tmp_path / "nothing.yaml")
    (tmp_path / "other.yaml").write_text("format: something\n")
    with pytest.raises(ValueError, match="is not a mems-sketch-rules/1"):
        load_deck(tmp_path / "other.yaml")


def test_deck_rules_are_checked_with_its_parameters(doc):
    doc.nodes.add(RectShape(name="beam", layer="device", x0=0, y0=0, x1=50, y1=2.5))
    assert kinds(doc) == [("fab.device_width", "width < 3 µm")]
    doc.process.set_deck_parameter("fab", "min_feature", 2)  # this project's value
    assert kinds(doc) == []
    assert doc.project.process.decks["fab"].parameters == {"min_feature": 2}
    doc.undo()
    assert kinds(doc) == [("fab.device_width", "width < 3 µm")]
    with pytest.raises(ValueError, match="no parameter 'width'"):
        doc.process.set_deck_parameter("fab", "width", 2)


def test_a_deck_rule_overridden_for_a_project(doc):
    doc.nodes.add(RectShape(name="beam", layer="device", x0=0, y0=0, x1=50, y1=2.5))
    doc.process.override_rule("fab", "device_width", {"value": 2}, reason="wider etch here")
    assert kinds(doc) == []
    use = doc.project.process.decks["fab"]
    assert use.overrides["device_width"].changes == {"value": 2}
    assert use.overrides["device_width"].reason == "wider etch here"
    doc.process.override_rule("fab", "device_width", {"value": "min_feature"})  # back: gone
    assert "device_width" not in use.overrides
    doc.process.override_rule("fab", "device_width", {"enabled": False})
    assert kinds(doc) == []
    doc.process.reset_rule("fab", "device_width")
    assert kinds(doc) == [("fab.device_width", "width < 3 µm")]
    with pytest.raises(ValueError, match="kind"):
        doc.process.override_rule("fab", "device_width", {"kind": "max_width"})


def test_a_renamed_layer_is_followed_by_an_override(doc):
    doc.process.set_layer("metal", Layer("top_metal", 3, 0))
    use = doc.project.process.decks["fab"]
    assert use.rule("metal_width").layers == ["top_metal"]
    assert use.overrides["metal_width"].reason == "layer renamed to top_metal"
    assert use.deck.rules["metal_width"].layers == ["metal"]  # the deck itself is unchanged


def test_decks_are_saved_with_the_project_relative_to_it(doc, tmp_path):
    doc.process.set_deck_parameter("fab", "min_feature", 2)
    doc.process.override_rule("fab", "device_space", {"severity": "warning"}, reason="r")
    doc.save(tmp_path / "project")
    text = (tmp_path / "project" / "process.yaml").read_text()
    assert "decks:\n  fab:\n    path: ../decks/fab.yaml\n" in text
    loaded = load(tmp_path / "project")
    assert loaded.process.decks == doc.project.process.decks
    # Moved together, the project still finds its deck.
    shutil.copytree(tmp_path, tmp_path.parent / "moved")
    moved = load(tmp_path.parent / "moved" / "project")
    assert moved.process.decks["fab"].deck.path == tmp_path.parent / "moved" / "decks" / "fab.yaml"


def test_a_deck_that_cannot_be_read_never_passes(doc, tmp_path):
    doc.save(tmp_path / "project")
    (tmp_path / "decks" / "fab.yaml").unlink()
    project = load(tmp_path / "project")  # the project still opens
    [found] = check(project)
    assert found.is_error and "no rule deck" in found.message and "not checked" in found.message


def test_reloading_a_deck_keeps_the_projects_settings(doc, tmp_path):
    doc.process.set_deck_parameter("fab", "min_feature", 2)
    deck = load_deck(tmp_path / "decks" / "fab.yaml")
    deck.parameters["min_feature"] = 5
    save_deck(deck, tmp_path / "decks" / "fab.yaml")
    doc.process.reload_deck("fab")
    use = doc.project.process.decks["fab"]
    assert use.deck.parameters["min_feature"] == 5 and use.parameters == {"min_feature": 2}


def test_bad_deck_parameters_are_reported(doc):
    doc.project.process.decks["fab"].parameters["min_feature"] = "nonsense * 2"
    found = check(doc.project)
    assert found and all("parameters of rule deck 'fab'" in v.message for v in found)


def test_the_projects_rules_saved_as_a_deck(tmp_path):
    session = EditSession()
    path = session.process.save_rules_as_deck(tmp_path / "mine.yaml", "mine", {"w": 2})
    deck = load_deck(path)
    assert deck.name == "mine" and deck.parameters == {"w": 2.0}
    assert set(deck.rules) == set(session.project.process.rules)
    other = EditSession()
    assert other.process.use_deck(path) == "mine"
    with pytest.raises(ValueError, match="already used"):
        other.process.use_deck(path)
    assert other.process.use_deck(path, name="mine_too") == "mine_too"
    other.process.remove_deck("mine_too")
    assert list(other.project.process.decks) == ["mine"]


def test_the_cli_shows_decks(doc, tmp_path, capsys):
    doc.process.set_deck_parameter("fab", "min_feature", 2)
    doc.process.override_rule("fab", "device_width", {"value": 1}, reason="test structure")
    doc.save(tmp_path / "project")
    assert main(["info", str(tmp_path / "project")]) == 0
    out = capsys.readouterr().out
    assert "rule deck fab:" in out
    assert "min_feature = 2  (the deck's: 3)" in out
    assert "fab.device_width" in out and "changed: test structure" in out


def test_a_single_file_project_keeps_its_decks(doc, tmp_path):
    save(doc.project, tmp_path / "design.json")
    assert load(tmp_path / "design.json").process.decks == doc.project.process.decks


# -- the GUI -----------------------------------------------------------------


@pytest.fixture
def panel(qtbot, doc):
    pytest.importorskip("PySide6")
    from mems_sketch.gui.rules_panel import RulesPanel

    widget = RulesPanel(doc)
    qtbot.addWidget(widget)
    widget.refresh()
    doc.changed.connect(widget.refresh)
    return widget


def row_of(table, text):
    return [table.item(r, 0).text() for r in range(table.rowCount())].index(text)


def test_the_panel_lists_deck_rules_and_edits_them_as_overrides(panel, doc):
    rules = panel.rules
    row = row_of(rules, "fab.device_width")
    assert rules.item(row, 6).text() == "fab"
    rules.item(row, 3).setText("value=1.5")
    use = doc.project.process.decks["fab"]
    assert use.overrides["device_width"].changes == {"value": 1.5}
    row = row_of(rules, "fab.device_width")
    assert rules.item(row, 6).text() == "fab (changed)"
    rules.item(row, 7).setText("test structures are narrower")
    assert use.overrides["device_width"].reason == "test structures are narrower"
    from PySide6.QtCore import Qt

    rules.item(row_of(rules, "fab.device_width"), 0).setCheckState(Qt.CheckState.Unchecked)
    assert use.rule("device_width").enabled is False
    rules.selectRow(row_of(rules, "fab.device_width"))
    panel._reset_rules()
    assert "device_width" not in use.overrides
    errors = []
    panel.error.connect(errors.append)
    rules.selectRow(row_of(rules, "fab.device_space"))
    panel._remove_rules()  # a deck's rule is turned off, not removed
    assert errors and "belongs to the rule deck" in errors[0]


def test_the_panel_sets_deck_parameters(panel, doc):
    decks = panel.decks
    assert decks.item(0, 0).text() == "fab"
    assert decks.item(0, 2).text() == "min_feature=3, space=min_feature + 1"
    decks.item(0, 2).setText("min_feature=2, space=min_feature + 1")
    assert doc.project.process.decks["fab"].parameters == {"min_feature": 2}
    decks.item(0, 2).setText("min_feature=3, space=min_feature + 1")  # the deck's again
    assert doc.project.process.decks["fab"].parameters == {}


def test_geometry_checked_against_a_deck_through_the_cli(tmp_path, capsys):
    write_deck(tmp_path / "decks" / "fab.yaml")
    session = EditSession()
    session.process.use_deck(tmp_path / "decks" / "fab.yaml")
    session.nodes.add(RectShape(name="beam", layer="device", x0=0, y0=0, x1=50, y1=2.5))
    session.save(tmp_path / "project")
    assert main(["check", str(tmp_path / "project")]) == 1
    assert "error: fab.device_width device: width < 3 µm" in capsys.readouterr().out
