"""Themes: JSON files of colours, built in, from packages and from the user's folder."""

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel

from mems_sketch.gui import icons, theme
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.settings import PreferencesDialog


@pytest.fixture
def window(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    return w


def _write(folder: Path, name: str, data) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def test_the_built_in_themes_are_complete(qapp):
    found = theme.load()
    assert {"light", "dark"} <= set(found)
    assert theme.problems() == []
    assert not found["light"].dark and found["dark"].dark
    assert found["dark"].ui["frame"] == "#26282c"
    assert found["light"].canvas["grid_alpha"] == (16, 34, 90)
    assert theme.choices() == {"system": "Same as the system", "dark": "Dark", "light": "Light"}


def test_a_user_theme_changes_some_colours_of_its_parent(qapp, tmp_path):
    _write(
        tmp_path / "themes",
        "ocean",
        {
            "name": "Ocean",
            "parent": "dark",
            "ui": {"accent": "#1abc9c"},
            "canvas": {"grid": "#00ffff"},
        },
    )
    ocean = theme.load()["ocean"]
    assert theme.problems() == []
    assert ocean.name == "Ocean" and ocean.dark  # dark, like its parent
    assert ocean.ui["accent"] == "#1abc9c"
    assert ocean.ui["frame"] == theme.get("dark").ui["frame"]  # the rest is the parent's
    assert ocean.canvas["grid"] == "#00ffff"
    assert theme.choices()["ocean"] == "Ocean"


@pytest.mark.parametrize(
    ("data", "why"),
    [
        ('{"name": "Broken",', "could not be read"),
        ({"name": "Bad", "parent": "light", "ui": {"accent": "blue"}}, "ui: accent"),
        ({"name": "Typo", "parent": "light", "ui": {"acent": "#000000"}}, "ui: acent"),
        ({"name": "Orphan", "parent": "nope"}, "its parent 'nope' is not a theme"),
        ({"name": "Alone", "ui": {"accent": "#000000"}}, "ui: frame"),  # no parent: all needed
        ({"name": "Odd", "parent": "light", "colour": "red"}, "colour"),
        ({"name": "Faint", "parent": "light", "canvas": {"grid_alpha": [0, 0, 999]}}, "0 to 255"),
    ],
)
def test_a_broken_theme_is_reported_and_left_out(qapp, tmp_path, data, why):
    _write(tmp_path / "themes", "mine", data)
    found = theme.load()
    assert "mine" not in found and {"light", "dark"} <= set(found)
    [problem] = theme.problems()
    assert problem.startswith("Theme 'mine' (") and why in problem, problem


def test_themes_whose_parents_go_round_are_left_out(qapp, tmp_path):
    _write(tmp_path / "themes", "a", {"name": "A", "parent": "b"})
    _write(tmp_path / "themes", "b", {"name": "B", "parent": "a"})
    assert not {"a", "b"} & set(theme.load())
    assert theme.problems() == [
        "Theme 'a' (" + str(tmp_path / "themes" / "a.json") + "): its parents go round in a "
        "circle: a → b → a",
        "Theme 'b' (" + str(tmp_path / "themes" / "b.json") + "): its parents go round in a "
        "circle: b → a → b",
    ]


def test_a_theme_whose_parent_is_broken_says_so(qapp, tmp_path):
    _write(tmp_path / "themes", "base", {"name": "Base", "parent": "light", "ui": {"text": "x"}})
    _write(tmp_path / "themes", "child", {"name": "Child", "parent": "base"})
    theme.load()
    assert "ui: text" in theme.problems()[0]
    assert theme.problems()[1].endswith("its parent 'base' cannot be used")


def test_a_user_theme_cannot_replace_a_built_in_one(qapp, tmp_path):
    _write(tmp_path / "themes", "dark", {"name": "Mine", "parent": "light"})
    assert theme.load()["dark"].name == "Dark"
    assert "that name is taken" in theme.problems()[0]


def test_packages_add_themes_through_an_entry_point(qapp, tmp_path, monkeypatch):
    class EntryPoint:
        def __init__(self, name, value, loaded):
            self.name, self.value, self._loaded = name, value, loaded

        def load(self):
            if isinstance(self._loaded, Exception):
                raise self._loaded
            return self._loaded

    path = _write(tmp_path / "pkg", "sand", {"name": "Sand", "parent": "light"})
    found = [
        EntryPoint("sand", "pkg:SAND", path),  # a JSON file
        EntryPoint("night", "pkg:NIGHT", {"name": "Night", "parent": "dark"}),  # the theme
        EntryPoint("dusk", "pkg:dusk", lambda: {"name": "Dusk", "parent": "dark"}),  # made
        EntryPoint("gone", "pkg:GONE", ImportError("no module named pkg")),
    ]
    monkeypatch.setattr(theme, "entry_points", lambda group: found)
    themes = theme.load()
    assert {"sand", "night", "dusk"} <= set(themes) and "gone" not in themes
    assert themes["night"].source == "package pkg:NIGHT"
    [problem] = theme.problems()
    assert "gone" in problem and "no module named pkg" in problem


def test_choosing_a_user_theme_colours_the_window_canvas_and_icons(window, tmp_path, qtbot):
    _write(
        tmp_path / "themes",
        "ocean",
        {
            "name": "Ocean",
            "parent": "dark",
            "ui": {"window": "#102030"},
            "canvas": {"background": "#001122"},
            "icons": {"fg": "#abcdef"},
        },
    )
    theme.load()
    window.settings.set("appearance/ui_theme", "ocean")
    assert QApplication.instance().palette().window().color().name() == "#102030"
    assert window.canvas.backgroundBrush().color().name() == "#001122"  # the canvas follows
    assert "#abcdef" in icons.svg("add")
    assert window.dark_action.isChecked()  # a dark theme: the canvas is dark
    window.settings.set("appearance/canvas_theme", "light")  # its own choice wins
    assert window.canvas.backgroundBrush().color().name() == "#ffffff"
    window.settings.set("appearance/ui_theme", "light")


def test_a_chosen_theme_that_is_gone_falls_back_to_the_system_one(window, qapp):
    window.settings.set("appearance/ui_theme", "ocean")  # not a theme: not kept
    assert window.settings.get("appearance/ui_theme") == "system"
    assert theme.resolve("ocean") in ("light", "dark")


def test_the_settings_page_shows_the_themes_folder_and_its_problems(window, tmp_path):
    _write(tmp_path / "themes", "mine", {"name": "Mine", "parent": "nope"})
    dialog = PreferencesDialog(window.settings, [], window)
    texts = [label.text() for label in dialog.findChildren(QLabel)]
    assert any(str(tmp_path / "themes") in t for t in texts)
    assert any("its parent 'nope' is not a theme" in t for t in texts)
    dialog.close()


def test_no_colours_are_written_into_the_interface_code():
    # Every colour comes from a theme, so a theme can change all of them.
    gui = Path(theme.__file__).parent
    pattern = re.compile(r"[\"']#(?:[0-9a-fA-F]{3}){1,2}\b")
    found = [
        f"{path.name}:{n}"
        for path in sorted(gui.glob("*.py"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line.split("  #")[0])
    ]
    assert found == []
