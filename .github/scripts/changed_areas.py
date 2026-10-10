"""Which parts of the program a change touches, for CI to run only what it
can affect (ci.yml). Reads changed paths, one per line, on standard input;
writes ``area=true|false`` lines (to $GITHUB_OUTPUT when it is set).

The parts depend on each other in one direction: the geometry library, the
engine on top of it, the Python package on top of the engine, the GUI on top
of everything (the architecture test keeps lower parts from importing the
GUI). A change runs its own part's checks and those of everything above it.
A path no rule knows runs everything, to be safe.
"""

from __future__ import annotations

import fnmatch
import os
import sys

# (pattern, area) in order: the first match decides.
RULES = [
    ("docs/*", "docs"),
    ("*.md", "docs"),
    ("LICENSE", "docs"),
    (".github/workflows/wheels.yml", "none"),  # these workflows check themselves
    (".github/workflows/app.yml", "none"),
    (".github/workflows/release-source.yml", "none"),
    ("packaging/*", "none"),
    ("src/geom/*", "geom"),
    ("CMakeLists.txt", "geom"),
    ("src/engine/*", "engine"),
    ("src/mems_sketch/gui/*", "gui"),
    ("tests/test_gui_*", "gui"),
    ("src/mems_sketch/*", "python"),
    ("tests/*", "python"),
    ("examples/*", "python"),
    ("benchmarks/*", "python"),
    ("pyproject.toml", "python"),
    (".gitignore", "none"),
]
# What each area brings in: itself and everything above it.
ABOVE = {
    "geom": {"geom", "engine", "python", "gui"},
    "engine": {"engine", "python", "gui"},
    "python": {"python", "gui"},
    "gui": {"gui"},
    "docs": set(),
    "none": set(),
}
AREAS = ["geom", "engine", "python", "gui"]


def areas(paths: list[str]) -> set[str]:
    found: set[str] = set()
    for path in paths:
        for pattern, area in RULES:
            if fnmatch.fnmatch(path, pattern):
                found |= ABOVE[area]
                break
        else:
            return set(AREAS)  # unknown: everything
    return found


def main() -> None:
    paths = [line.strip() for line in sys.stdin if line.strip()]
    touched = areas(paths) if paths else set(AREAS)  # nothing to compare with: everything
    lines = [f"{area}={'true' if area in touched else 'false'}" for area in AREAS]
    print("\n".join(lines))
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
