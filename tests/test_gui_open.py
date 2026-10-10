"""Opening a project: the file first, then where to open it, then unsaved changes."""

import shutil
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QFileDialog, QMessageBox

from mems_sketch.gui.app import MainWindow

EXAMPLES = Path(__file__).parent.parent / "examples"


@pytest.fixture
def projects(tmp_path):
    for name in ("resonator", "libraries"):
        shutil.copytree(
            EXAMPLES / name, tmp_path / name, ignore=shutil.ignore_patterns(".mems-sketch")
        )
    return tmp_path


@pytest.fixture
def window(qtbot, monkeypatch, projects):
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    asked = w.asked = []  # the questions asked, in order
    w.answers = {}

    def question(*args, **kwargs):
        asked.append("save?")
        return w.answers.get("save?", QMessageBox.StandardButton.Cancel)

    def where(path):
        asked.append("where?")
        return w.answers.get("where?")

    monkeypatch.setattr(QMessageBox, "question", question)
    monkeypatch.setattr(w, "ask_where_to_open", where)
    w.started = []
    monkeypatch.setattr(w, "open_in_new_window", w.started.append)
    return w


def choose(monkeypatch, path):
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))


def edited(window, projects):
    window.open_project(str(projects / "resonator"))
    window.add_primitive("rect")
    assert window.document.dirty


def test_an_empty_window_opens_the_chosen_project_without_questions(window, monkeypatch, projects):
    choose(monkeypatch, projects / "resonator" / "project.yaml")
    window.open_project()
    assert window.document.path == projects / "resonator"
    assert window.asked == []


def test_cancelling_the_file_dialog_asks_nothing(window, monkeypatch, projects):
    edited(window, projects)
    choose(monkeypatch, "")
    window.open_project()
    assert window.asked == [] and window.document.dirty


def test_the_file_comes_first_then_where_then_saving(window, monkeypatch, projects):
    edited(window, projects)
    choose(monkeypatch, projects / "resonator" / "project.yaml")
    window.answers = {"where?": "here", "save?": QMessageBox.StandardButton.Discard}
    window.open_project()
    assert window.asked == ["where?", "save?"]
    assert not window.document.dirty  # reopened from the file


def test_a_new_window_leaves_this_one_as_it_is(window, monkeypatch, projects):
    edited(window, projects)
    chosen = projects / "resonator" / "project.yaml"
    choose(monkeypatch, chosen)
    window.answers = {"where?": "new"}
    window.open_project()
    assert window.started == [str(chosen)]
    assert window.asked == ["where?"] and window.document.dirty  # nothing asked about saving


def test_cancelling_where_or_saving_keeps_the_project(window, monkeypatch, projects):
    edited(window, projects)
    choose(monkeypatch, projects / "resonator" / "project.yaml")
    window.open_project()  # "where?" cancelled
    window.answers = {"where?": "here"}  # then "save?" cancelled
    window.open_project()
    assert window.asked == ["where?", "where?", "save?"] and window.document.dirty


def test_new_project_asks_about_saving_after_the_wizard(window, monkeypatch, projects):
    edited(window, projects)
    monkeypatch.setattr(window, "show_dialog", lambda dialog: 0)  # the wizard cancelled
    window.new_project()
    assert window.asked == [] and window.document.dirty
