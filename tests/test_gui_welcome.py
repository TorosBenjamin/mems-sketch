"""The Welcome window (#142): the projects opened lately, kept in the settings;
search, pin, a missing folder located again; New project and Open…; shown on
start without a file and when a project is closed."""

import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from mems_sketch.editing import EditSession
from mems_sketch.gui import app as app_module
from mems_sketch.gui import recent_projects
from mems_sketch.gui.app import MainWindow, show_welcome
from mems_sketch.gui.recent_projects import Recent
from mems_sketch.gui.welcome import WelcomeWindow


@pytest.fixture(autouse=True)
def no_questions(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)


def project(folder: Path) -> Path:
    """A saved project in ``folder``."""
    EditSession().save(folder)
    return folder


def seed(*items: Recent) -> None:
    recent_projects._store(list(items))


# -- the list in the settings -------------------------------------------------------


def test_opening_remembers_a_project_first_and_pinned_ones_stay_on_top(tmp_path):
    for name in ("a", "b", "c"):
        (tmp_path / name).mkdir()
        recent_projects.remember(tmp_path / name, name)
        time.sleep(0.001)
    assert [r.name for r in recent_projects.load()] == ["c", "b", "a"]
    recent_projects.pin(str((tmp_path / "a").resolve()))
    recent_projects.remember(tmp_path / "b" / "project.yaml", "b")  # its manifest: the folder
    assert [r.name for r in recent_projects.load()] == ["a", "b", "c"]
    recent_projects.forget(str((tmp_path / "c").resolve()))
    assert [r.name for r in recent_projects.load()] == ["a", "b"]


def test_the_list_keeps_the_newest(tmp_path):
    for i in range(recent_projects.MOST + 5):
        recent_projects.remember(tmp_path / f"p{i}", f"p{i}")
    names = [r.name for r in recent_projects.load()]
    assert len(names) == recent_projects.MOST and names[0] == f"p{recent_projects.MOST + 4}"


def test_a_missing_folder_is_located_again(tmp_path):
    seed(Recent(str(tmp_path / "gone"), "gone", opened=1.0))
    assert recent_projects.load()[0].missing
    project(tmp_path / "found")
    recent_projects.locate(str(tmp_path / "gone"), tmp_path / "found")
    (moved,) = recent_projects.load()
    assert moved.path == str((tmp_path / "found").resolve()) and not moved.missing


def test_the_branch_is_read_from_the_repository(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/feature/x\n")
    (tmp_path / "chip").mkdir()
    assert recent_projects.git_branch(tmp_path / "chip") == "feature/x"


def test_when_is_said_in_words():
    now = 1_000_000.0
    assert recent_projects.when(now - 5, now) == "just now"
    assert recent_projects.when(now - 300, now) == "5 minutes ago"
    assert recent_projects.when(now - 2 * 3600, now) == "2 hours ago"
    assert recent_projects.when(now - 30 * 3600, now) == "yesterday"
    assert recent_projects.when(now - 5 * 86400, now) == "5 days ago"


# -- the window -----------------------------------------------------------------------


@pytest.fixture
def welcome(qtbot, tmp_path):
    project(tmp_path / "comb_chip")
    project(tmp_path / "gyro")
    seed(
        Recent(str(tmp_path / "comb_chip"), "comb_chip", opened=3.0),
        Recent(str(tmp_path / "gyro"), "gyro", opened=2.0, pinned=True),
        Recent(str(tmp_path / "old"), "old", opened=1.0),
    )
    window = WelcomeWindow("Version test")
    qtbot.addWidget(window)
    window.show()
    return window


def test_the_list_pinned_first_and_a_missing_one_marked(welcome):
    rows = welcome.rows()
    assert [r.name for r in rows] == ["gyro", "comb_chip", "old"]
    assert [r.missing for r in rows] == [False, False, True]


def test_search_narrows_the_list(welcome):
    welcome.search.setText("comb")
    assert [r.name for r in welcome.rows()] == ["comb_chip"]
    welcome.search.setText("nothing like it")
    assert welcome.rows() == [] and welcome.empty.isVisible()


def test_enter_opens_the_chosen_project(welcome, qtbot):
    welcome.search.setText("comb")
    with qtbot.waitSignal(welcome.open_project) as opened:
        QApplication.sendEvent(
            welcome.search,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier),
        )
    assert opened.args[0].endswith("comb_chip")


def test_pinning_and_removing_from_the_menu(welcome):
    comb = next(r for r in welcome.rows() if r.name == "comb_chip")
    actions = {a.text(): a for a in welcome.menu_for(comb).actions()}
    actions["Pin to the top"].trigger()
    assert [r.name for r in welcome.rows() if r.pinned] == ["comb_chip", "gyro"]  # newest first
    old = next(r for r in welcome.rows() if r.name == "old")
    {a.text(): a for a in welcome.menu_for(old).actions()}["Remove from the list"].trigger()
    assert "old" not in [r.name for r in welcome.rows()]


def test_a_missing_project_is_located_when_opened(welcome, tmp_path, monkeypatch):
    project(tmp_path / "old_moved")
    monkeypatch.setattr(welcome, "pick_folder", lambda title: str(tmp_path / "old_moved"))
    item = next(
        welcome.list.item(i) for i in range(welcome.list.count()) if welcome.rows()[i].name == "old"
    )
    welcome.list.itemActivated.emit(item)
    old = next(r for r in welcome.rows() if r.name == "old")
    assert not old.missing


def test_open_asks_the_file_picker(welcome, tmp_path, monkeypatch, qtbot):
    monkeypatch.setattr(welcome, "pick_file", lambda: str(tmp_path / "gyro" / "project.yaml"))
    with qtbot.waitSignal(welcome.open_project) as opened:
        welcome.open_button.click()
    assert opened.args[0].endswith("project.yaml")


# -- where it shows -----------------------------------------------------------------


def test_a_project_opened_from_welcome_gets_a_window(tmp_path, qtbot):
    project(tmp_path / "chip")
    welcome = show_welcome()
    welcome.open_project.emit(str(tmp_path / "chip"))
    windows = [w for w in app_module._windows if isinstance(w, MainWindow)]
    assert windows and windows[-1].document.path == (tmp_path / "chip")
    assert recent_projects.load()[0].path == str((tmp_path / "chip").resolve())
    for window in windows:
        window.close()
        app_module._windows.remove(window)


def test_closing_a_project_shows_welcome(tmp_path, qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path / "p"))
    (tmp_path / "p").mkdir()
    window.save_project_as()
    assert recent_projects.load()[0].path == str((tmp_path / "p").resolve())  # remembered
    shown = []
    monkeypatch.setattr(app_module, "show_welcome", lambda: shown.append(True))
    window.close_project()
    assert shown and not window.isVisible()
