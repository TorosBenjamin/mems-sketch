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


# -- #259: files, import and the command line -----------------------------------


def test_export_makes_missing_folders_and_names_a_missing_extension(resonator, tmp_path, capsys):
    assert main(["export", str(resonator), str(tmp_path / "out" / "sub" / "r.gds")]) == 0
    assert (tmp_path / "out" / "sub" / "r.gds").exists()
    captured = capsys.readouterr()
    assert "building top…" in captured.err and "writing" in captured.err  # while it works
    assert main(["export", str(resonator), str(tmp_path / "noext")]) == 2
    assert "has no extension to tell the format by: give one (" in capsys.readouterr().err


def test_a_layout_or_another_file_is_not_opened_as_a_project(resonator, tmp_path, capsys):
    main(["export", str(resonator), str(tmp_path / "r.gds")])
    capsys.readouterr()
    assert main(["info", str(tmp_path / "r.gds")]) == 2
    assert "is a layout, not a project: import it" in capsys.readouterr().err
    (tmp_path / "notes.txt").write_text("hello")
    assert main(["info", str(tmp_path / "notes.txt")]) == 2
    assert "is not a project" in capsys.readouterr().err


def test_a_library_asks_for_a_component_and_lists_them(tmp_path, capsys):
    assert main(["new", str(tmp_path / "lib"), "--library"]) == 0
    assert main(["check", str(tmp_path / "lib")]) == 2
    error = capsys.readouterr().err
    assert "'lib' is a library, with no top component: name one with --component" in error
    assert "(its components: component1)" in error


def test_new_on_a_library_process_and_new_process_lists_them(tmp_path, capsys):
    assert main(["new", str(tmp_path / "p"), "--process", "mems.surface"]) == 0
    assert load(tmp_path / "p").process_name == "mems.surface"
    assert main(["new", str(tmp_path / "q"), "--process", "nope"]) == 2
    assert "mems.surface, mems.polysilicon, mems.soi" in capsys.readouterr().err
    assert main(["new-process", str(tmp_path / "f"), "--from", "soi"]) == 2
    assert "mems.soi, or empty" in capsys.readouterr().err


def test_info_shows_the_grid_and_whole_numbers_and_one_component(resonator, capsys):
    assert main(["info", str(resonator)]) == 0
    out = capsys.readouterr().out
    assert "(grid 0.001 µm, curves within 0.005 µm)" in out
    assert "process.undercut = 2\n" in out  # as the file says it, not 2.0
    assert main(["info", str(resonator), "--component", "mems.anchor"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("component mems.anchor: Square pad")
    assert "  size = 40 [> 0]: Pad edge length (µm)" in out
    assert main(["info", str(resonator), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["process"]["grid_um"] == 0.001 and "top" in data["components"]
    assert main(["info", str(resonator), "--component", "mems.anchor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["parameters"]["size"]["default"] == 40


def test_formats_say_the_grid_comes_from_the_process_and_grid_is_understood(
    resonator, tmp_path, capsys
):
    assert main(["formats"]) == 0
    assert "grid_um=0.001 µm (default: the process's grid)" in capsys.readouterr().out
    assert main(["export", str(resonator), str(tmp_path / "r.gds"), "-O", "grid=0.002"]) == 0


def test_a_cell_name_some_tools_refuse_is_warned_of(resonator, tmp_path, capsys):
    path = str(tmp_path / "r.gds")
    assert main(["export", str(resonator), path, "-O", "top_cell=My Chip"]) == 0
    assert "warning: cell 'My Chip' is written as it is" in capsys.readouterr().err
    assert main(["export", str(resonator), path, "-O", "top_cell=CHIP_1"]) == 0
    assert "warning" not in capsys.readouterr().err


def test_info_into_a_closed_pipe_is_quiet(resonator):
    import subprocess
    import sys

    run = subprocess.run(
        f'"{sys.executable}" -m mems_sketch.cli info "{resonator}" | head -1',
        shell=True,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.stdout.startswith("project resonator")
    assert "Broken pipe" not in run.stderr
