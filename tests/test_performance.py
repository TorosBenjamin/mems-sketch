"""Performance settings (#78, QS-11), rule checks left until a drag ends (#79,
QS-9) and building ahead while a value is dragged (#96, QS-10)."""

from concurrent.futures import Future

import pytest

from mems_sketch import RefShape, machine
from mems_sketch.editing import EditSession
from mems_sketch.editing.speculation import AheadSettings, Speculator


class Inline:
    """An executor that runs at once, so the tests see what was built."""

    def submit(self, fn, *args):
        future = Future()
        future.set_result(fn(*args))
        return future

    def shutdown(self, **_):
        pass


@pytest.fixture
def comb() -> EditSession:
    session = EditSession()
    session.nodes.add(RefShape(name="comb", component="mems.comb_drive"))
    session.results.geometry()
    return session


# -- #78: the cache's memory and the threads ------------------------------------------


def test_the_cache_weighs_what_it_keeps_and_drops_what_was_built_ahead_first(comb):
    engine = comb.engine
    assert engine.cache_bytes > 0 and engine.speculative_bytes == 0
    ahead = engine.trial(engine.project, speculative=True)
    ahead.build_ahead("mems.comb_drive", {"fingers": 5})
    assert engine.speculative_bytes > 0
    engine.set_cache_limits(10**12, 0)  # no room for what is built ahead
    assert engine.speculative_bytes == 0
    assert engine.cache_bytes > 0  # the real builds stay


def test_a_build_asked_for_for_real_is_kept(comb):
    engine = comb.engine
    ahead = engine.trial(engine.project, speculative=True)
    ahead.build_ahead("mems.comb_drive", {"fingers": 7})
    before = engine.speculative_bytes
    engine.build("mems.comb_drive", {"fingers": 7}).geometry  # noqa: B018 - a cache hit
    assert engine.speculative_bytes < before


def test_threads_can_be_limited():
    from mems_sketch import engine

    engine.set_threads(1)
    assert engine.threads() == 1
    engine.set_threads(0)
    assert engine.threads() >= 1


def test_the_machines_defaults():
    assert machine.cores() >= 1 and machine.memory() > 0
    assert 256 <= machine.default_cache_mb() <= 4096


# -- #96: building ahead ------------------------------------------------------------


@pytest.fixture
def speculator(comb, monkeypatch):
    monkeypatch.setattr(machine, "has_spare", lambda needed=1.0: True)
    found = Speculator(comb, AheadSettings(ahead=2, max_seconds=5))
    monkeypatch.setattr(found, "_pool", lambda: Inline())
    return found


def test_the_next_whole_numbers_are_built_ahead(speculator):
    queued = speculator.around("mems.comb_drive", "fingers", 6, took=0.1, limits=(5, None))
    assert queued == 3  # 7, 5, 8 (4 is below the limit)
    assert [p["fingers"] for p in speculator.built] == [7, 5, 8]
    engine = speculator.session.engine
    assert engine.speculative_bytes > 0


def test_nothing_is_built_ahead_of_a_slow_design_or_when_off(speculator):
    assert speculator.around("mems.comb_drive", "fingers", 6, took=30.0) == 0
    assert speculator.around("mems.comb_drive", "fingers", 6, took=None) == 0
    speculator.settings.ahead = 0
    assert speculator.around("mems.comb_drive", "fingers", 6, took=0.1) == 0
    assert speculator.built == []


def test_nothing_is_built_ahead_while_the_machine_is_busy(speculator, monkeypatch):
    monkeypatch.setattr(machine, "has_spare", lambda needed=1.0: False)
    speculator.around("mems.comb_drive", "fingers", 6, took=0.1)
    assert speculator.built == []


def test_a_real_change_stops_building_ahead(speculator, monkeypatch):
    started = []

    class Later(Inline):
        def submit(self, fn, *args):
            started.append((fn, args))
            return Future()

    monkeypatch.setattr(speculator, "_pool", lambda: Later())
    speculator.around("mems.comb_drive", "fingers", 6, took=0.1)
    speculator.cancel()  # what MainWindow.refresh does first
    for fn, args in started:
        fn(*args)  # run late: each sees it was stopped
    assert speculator.built == []


# -- the window -------------------------------------------------------------------------


@pytest.fixture
def window(qtbot, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QMessageBox

    from mems_sketch.gui.app import MainWindow

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    return w


def test_the_performance_settings_apply(window):
    window.settings.set("performance/cache_mb", 512)
    window.settings.set("performance/ahead_cache_percent", 10)
    window.settings.set("performance/build_ahead", "4")
    window.settings.set("performance/threads", 2)
    from mems_sketch import engine

    assert engine.threads() == 2
    assert window.speculator.settings.ahead == 4
    window.settings.set("performance/threads", 0)


def test_rules_are_checked_when_a_drag_ends(window):
    from mems_sketch import RectShape

    session = window.document
    session.parameters.set("w", 1)  # a 1 µm beam: narrower than the 2 µm rule
    session.nodes.add(RectShape(name="beam", layer="device", x0=0, y0=0, x1=40, y1="w"))
    view = window.view
    assert view.violations  # checked at once, as usual
    session.dragging = True
    session.set_trial("w", 1.5)  # a step of the drag: redrawn, not checked
    assert view.checking and view.violations == []
    session.dragging = False
    window.parameters.drag_finished.emit()
    assert not view.checking and view.violations


def test_checking_during_a_drag_can_be_kept(window):
    from mems_sketch import RectShape

    window.settings.set("canvas/drag_skips_checks", False)
    session = window.document
    session.parameters.set("w", 1)
    session.nodes.add(RectShape(name="beam", layer="device", x0=0, y0=0, x1=40, y1="w"))
    session.dragging = True
    session.set_trial("w", 1.5)
    assert window.view.violations and not window.view.checking
    session.dragging = False


def test_dragging_a_whole_number_builds_ahead(window, monkeypatch):
    from mems_sketch.core.user_component import ParamDef

    asked = []
    monkeypatch.setattr(window.speculator, "around", lambda *a, **k: asked.append(a))
    whole = ParamDef(name="fingers", default=6, integer=True)
    window.parameters.value_dragged.emit(whole, 7.0)
    assert asked and asked[0][1:3] == ("fingers", 7.0)
    asked.clear()
    window.parameters.value_dragged.emit(ParamDef(name="gap", default=2.0), 2.5)
    assert asked == []  # only whole numbers have neighbours to build
