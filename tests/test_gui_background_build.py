"""A big design builds in the background: the editor goes on meanwhile (QS-8)."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox

from mems_sketch import RectShape
from mems_sketch.core.project import Project
from mems_sketch.core.user_component import ComponentDef
from mems_sketch.editing import EditSession
from mems_sketch.gui import app
from mems_sketch.gui.app import BACKGROUND_BUILD_S, MainWindow


def area(geometry) -> float:
    region = geometry.layers.get("device")
    return 0.0 if region is None else region.area() / 1e6


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1000, 700)
    w.show()
    qtbot.waitExposed(w)
    w.document.parameters.set("width", 4.0)
    w.document.nodes.add(RectShape(name="bar", layer="device", x0=0, y0=0, x1="width", y1=2))
    return w


def slow(window) -> None:
    """As if the component had taken long to build: a change builds in the background."""
    window._took({window.view.component: BACKGROUND_BUILD_S * 2})


def test_a_slow_design_shows_the_last_result_until_the_new_one_is_built(window, qtbot, monkeypatch):
    slow(window)
    assert not window.document.check_builds  # an edit does not wait to see that it builds
    held, builder = threading.Event(), ThreadPoolExecutor(max_workers=1)
    builder.submit(held.wait)  # the build waits until the test lets it go
    monkeypatch.setattr(app, "builds", lambda: builder)
    view = window.view
    window.run(lambda: window.document.parameters.update("width", default=10.0))
    assert view.updating and area(view.shown) == pytest.approx(8)  # the last result
    window._show_updating(window.document.results.version)  # as when it takes a while
    assert "updating…" in view.canvas.caption_details.text()
    assert not window.properties.isEnabled()  # it shows built geometry: greyed out meanwhile
    held.set()
    qtbot.waitUntil(lambda: not view.updating, timeout=10_000)
    assert area(view.shown) == pytest.approx(20)
    assert window.properties.isEnabled()
    assert "updating…" not in view.canvas.caption_details.text()


def test_a_quick_change_to_a_slow_design_is_built_in_the_background_too(window, qtbot):
    slow(window)
    window._took({window.view.component: 0.001})  # this one was quick, others were not
    assert not window.document.check_builds
    window.run(lambda: window.document.parameters.update("width", default=9.0))
    assert window.view.updating
    qtbot.waitUntil(lambda: not window.view.updating, timeout=10_000)


def test_only_the_newest_of_quick_changes_is_shown(window, qtbot):
    slow(window)
    for width in (5.0, 6.0, 7.0):
        window.run(lambda w=width: window.document.parameters.update("width", default=w))
    qtbot.waitUntil(lambda: not window.view.updating, timeout=10_000)
    assert area(window.view.shown) == pytest.approx(14)


def test_a_change_that_does_not_build_keeps_the_last_picture_and_says_why(window, qtbot):
    slow(window)
    view = window.view
    window.run(lambda: window.document.parameters.update("width", default=8.0))
    qtbot.waitUntil(lambda: not view.updating, timeout=10_000)
    bad = RectShape(name="bad", layer="device", x0=0, y0=0, x1="nope", y1=1)
    window.run(lambda: window.document.nodes.add(bad))  # refused only where builds are checked
    qtbot.waitUntil(lambda: not view.updating, timeout=10_000)
    assert any("nope" in e for e in view.errors)
    assert area(view.shown) == pytest.approx(16)  # still the last picture that built


def test_a_quick_design_is_built_at_once(window):
    assert window.document.check_builds
    window.run(lambda: window.document.parameters.update("width", default=3.0))
    assert not window.view.updating
    assert area(window.view.shown) == pytest.approx(6)


def test_what_is_asked_for_meanwhile_waits_for_the_background_build():
    session = EditSession(
        Project(
            components={
                "top": ComponentDef(
                    name="top", shapes=[RectShape(layer="device", x0=0, y0=0, x1=3, y1=1)]
                )
            }
        )
    )
    session.active = "top"
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = session.results.build_in_background(["top"], executor)
        assert session.results.inspection("top")  # waited for it, or it was done
        assert future.result() is True
        assert session.results.ready()
        session.changed.emit()  # a newer version: a build of the old one is not used
        stale = session.results.build_in_background(["top"], executor)
        session.changed.emit()
        assert stale.result() is False


def test_without_build_checks_an_edit_that_does_not_build_is_kept():
    session = EditSession()
    bad = RectShape(name="bad", layer="device", x0=0, y0=0, x1="nope", y1=1)
    with pytest.raises(ValueError, match="nope"):
        session.nodes.add(bad)
    session.check_builds = False
    session.nodes.add(bad)
    assert any("nope" in p for p in session.results.problems())


def plate_session() -> EditSession:
    """A plate with 3,600 round holes: a build that takes a while."""
    from mems_sketch import ArrayModifier, BooleanShape, CircleShape

    holes = CircleShape(
        layer="device",
        x=1.25,
        y=1.25,
        radius="r",
        modifiers=[ArrayModifier(rows=60, columns=60, dx=2.5, dy=2.5)],
    )
    plate = BooleanShape(
        op="subtract", a=[RectShape(layer="device", x0=0, y0=0, x1=150, y1=150)], b=[holes]
    )
    session = EditSession()
    session.parameters.set("r", 0.5)
    session.nodes.add(plate)
    return session


def test_a_cancelled_engine_builds_no_more():
    from mems_sketch import engine as engine_module

    session = plate_session()
    engine = session._engine.trial(session._snapshot())
    engine.cancel()
    with pytest.raises(engine_module.BuildCancelled):
        engine.build(session.active).geometry  # noqa: B018


def test_a_change_stops_the_background_build_of_the_design_before_it():
    session = plate_session()
    session.check_builds = False  # as the editor has it for a big design: edits do not build
    with ThreadPoolExecutor(max_workers=1) as executor:
        session.parameters.update("r", default=0.6)  # not built yet
        started = time.perf_counter()
        whole = session.results.build_in_background([session.active], executor)
        assert whole.result() is True
        full = time.perf_counter() - started

        session.parameters.update("r", default=0.7)
        stale = session.results.build_in_background([session.active], executor)
        time.sleep(full / 10)
        started = time.perf_counter()
        session.parameters.update("r", default=0.8)  # stops the build of r = 0.7
        assert stale.result() is False
        assert time.perf_counter() - started < full / 2
