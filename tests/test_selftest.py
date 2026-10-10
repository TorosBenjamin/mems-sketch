"""What the release packages are checked with: mems-sketch --version and
--self-test (.github/workflows/app.yml)."""

import pytest

pytest.importorskip("PySide6")

from mems_sketch import __version__
from mems_sketch.gui.app import main


def test_version(capsys):
    assert main(["mems-sketch", "--version"]) == 0
    assert capsys.readouterr().out.strip() == f"mems-sketch {__version__}"
    assert __version__ != "0.0.0"  # the installed package's version


def test_self_test_passes_and_logs(qapp, tmp_path, monkeypatch, capsys):
    log = tmp_path / "self-test.log"
    monkeypatch.setenv("MEMS_SKETCH_SELFTEST_LOG", str(log))
    assert main(["mems-sketch", "--self-test"]) == 0
    lines = log.read_text().splitlines()
    assert lines[0] == f"mems-sketch {__version__}"
    assert lines[-1] == "self-test passed"
    assert any(line.startswith("rules checked in another process") for line in lines)


def test_a_failing_self_test_says_so(tmp_path, monkeypatch):
    from mems_sketch.gui import selftest

    def broken(window=True):
        raise RuntimeError("the engine is missing")

    monkeypatch.setattr(selftest, "run", broken)
    log = tmp_path / "self-test.log"
    monkeypatch.setenv("MEMS_SKETCH_SELFTEST_LOG", str(log))
    assert main(["mems-sketch", "--self-test"]) == 1
    text = log.read_text()
    assert "the engine is missing" in text and text.rstrip().endswith("self-test FAILED")
