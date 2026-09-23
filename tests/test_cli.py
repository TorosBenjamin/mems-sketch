import json
import shutil
from pathlib import Path

import klayout.db as kdb
import pytest

from mems_sketch.cli import main

EXAMPLES = Path(__file__).parent.parent / "examples"


@pytest.fixture
def resonator(tmp_path) -> Path:
    shutil.copytree(EXAMPLES / "resonator", tmp_path / "resonator")
    shutil.copytree(EXAMPLES / "libraries", tmp_path / "libraries")
    return tmp_path / "resonator"


def test_new_and_info(tmp_path, capsys):
    assert main(["new", str(tmp_path / "p")]) == 0
    assert (tmp_path / "p" / "components" / "top.yaml").exists()
    assert main(["new", str(tmp_path / "p")]) == 2  # refuses to overwrite
    assert main(["info", str(tmp_path / "p")]) == 0
    assert "device" in capsys.readouterr().out


def test_check_passes_and_fails_with_overrides(resonator, capsys):
    assert main(["check", str(resonator)]) == 0
    assert main(["check", str(resonator), "--set", "w_finger=1", "--json"]) == 1
    violations = json.loads(capsys.readouterr().out)
    assert violations and {v["rule"] for v in violations} == {"min_width", "min_space"}


def test_check_a_single_component(resonator):
    assert main(["check", str(resonator), "--component", "std.perforated_plate"]) == 0


def test_export_with_etch_compensation(resonator, tmp_path):
    drawn, compensated = tmp_path / "drawn.gds", tmp_path / "comp.gds"
    assert main(["export", str(resonator), str(drawn)]) == 0
    assert main(["export", str(resonator), str(compensated), "--etch", "compensated"]) == 0

    def area(path):
        layout = kdb.Layout()
        layout.read(str(path))
        return kdb.Region(layout.top_cell().begin_shapes_rec(layout.layer(1, 0))).area()

    assert area(compensated) > area(drawn)


def test_errors_exit_with_status_2(resonator, capsys):
    assert main(["check", str(resonator), "--set", "nonsense"]) == 2
    assert main(["export", str(resonator / "nowhere"), "x.gds"]) == 2
    assert "error:" in capsys.readouterr().err


def test_examples_are_up_to_date(tmp_path):
    """The committed example YAML is exactly what build_examples.py produces."""
    import runpy

    shutil.copy(EXAMPLES / "build_examples.py", tmp_path / "build_examples.py")
    runpy.run_path(str(tmp_path / "build_examples.py"), run_name="__main__")
    for generated in tmp_path.rglob("*.yaml"):
        committed = EXAMPLES / generated.relative_to(tmp_path)
        assert committed.read_text() == generated.read_text(), committed
