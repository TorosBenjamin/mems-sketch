"""CI runs only what a change can affect (.github/scripts/changed_areas.py)."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "changed_areas.py"
spec = importlib.util.spec_from_file_location("changed_areas", SCRIPT)
changed_areas = importlib.util.module_from_spec(spec)
spec.loader.exec_module(changed_areas)

EVERYTHING = {"geom", "engine", "python", "gui"}


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["docs/user/README.md", "README.md"], set()),
        (["src/mems_sketch/gui/canvas.py", "tests/test_gui_window.py"], {"gui"}),
        (["src/mems_sketch/core/region.py"], {"python", "gui"}),
        (["tests/test_rules.py"], {"python", "gui"}),
        (["src/engine/src/eval/evaluator.cpp"], {"engine", "python", "gui"}),
        (["src/geom/src/grid.cpp"], EVERYTHING),
        (["packaging/entry.py", ".github/workflows/app.yml"], set()),  # app.yml checks those
        ([".github/workflows/ci.yml"], EVERYTHING),  # CI itself: everything
        (["something/new.txt"], EVERYTHING),  # unknown: everything, to be safe
        (["docs/x.md", "src/engine/CMakeLists.txt"], {"engine", "python", "gui"}),
    ],
)
def test_a_change_runs_its_part_and_everything_above_it(paths, expected):
    assert changed_areas.areas(paths) == expected


def test_every_area_is_known_to_the_workflow():
    workflow = (SCRIPT.parent.parent / "workflows" / "ci.yml").read_text()
    for area in changed_areas.AREAS:
        assert f"steps.areas.outputs.{area}" in workflow
