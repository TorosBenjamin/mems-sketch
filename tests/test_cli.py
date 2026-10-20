import json
import shutil
from pathlib import Path

import pytest
from helpers import read_gds

from mems_sketch.cli import main
from mems_sketch.storage import load

EXAMPLES = Path(__file__).parent.parent / "examples"


@pytest.fixture
def resonator(tmp_path) -> Path:
    shutil.copytree(
        EXAMPLES / "resonator",
        tmp_path / "resonator",
        ignore=shutil.ignore_patterns(".mems-sketch"),
    )
    return tmp_path / "resonator"


def test_new_and_info(tmp_path, capsys):
    assert main(["new", str(tmp_path / "p")]) == 0
    assert (tmp_path / "p" / "components" / "top" / "component.yaml").exists()
    assert main(["new", str(tmp_path / "p")]) == 2  # refuses to overwrite
    assert main(["info", str(tmp_path / "p")]) == 0
    assert "device" in capsys.readouterr().out


def test_convert_never_overwrites_a_project(resonator, tmp_path):
    target = tmp_path / "r.json"
    assert main(["convert", str(resonator), str(target)]) == 0
    assert main(["convert", str(resonator), str(target)]) == 2  # a one-file project too
    assert main(["convert", str(resonator), str(tmp_path / "copy")]) == 0
    assert main(["convert", str(target), str(tmp_path / "copy")]) == 2


def test_check_passes_and_fails_with_overrides(resonator, capsys):
    assert main(["check", str(resonator)]) == 0
    assert main(["check", str(resonator), "--set", "w_finger=1", "--json"]) == 1
    violations = json.loads(capsys.readouterr().out)
    errors = {v["kind"] for v in violations if v["severity"] == "error"}
    assert errors == {"min_width", "min_space"}


def test_check_a_single_component(resonator):
    assert main(["check", str(resonator), "--component", "mems.perforated_plate"]) == 0


def test_export_writes_the_drawn_geometry(resonator, tmp_path):
    out = tmp_path / "out.gds"
    assert main(["export", str(resonator), str(out)]) == 0
    layout = read_gds(out)
    assert sum(p.area() for p in layout.flat(layout.top_cells()[0], (1, 0))) > 0


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


def test_new_process_starts_a_process_library(tmp_path, capsys):
    folder = tmp_path / "fab"
    assert main(["new-process", str(folder), "--from", "mems.polysilicon"]) == 0
    library = load(folder)
    assert library.top is None and list(library.processes) == ["fab"]
    assert "poly1_min_width" in library.processes["fab"].rules
    assert "mems.polysilicon" in library.processes["fab"].description
    assert main(["new-process", str(tmp_path / "blank"), "--from", "empty"]) == 0
    assert load(tmp_path / "blank").processes["blank"].rules == {}
    assert main(["new-process", str(tmp_path / "x"), "--from", "mems.nope"]) != 0
    assert "no process 'mems.nope'" in capsys.readouterr().err
