"""The backend (everything outside mems_sketch.gui) must never depend on the GUI."""

import ast
import subprocess
import sys
from pathlib import Path

PACKAGE = Path(__file__).parent.parent / "src" / "mems_sketch"
FORBIDDEN = ("PySide6", "PyQt5", "PyQt6", "mems_sketch.gui")


def backend_modules():
    return [p for p in PACKAGE.rglob("*.py") if "gui" not in p.relative_to(PACKAGE).parts]


def test_backend_source_has_no_gui_imports():
    offenders = []
    for path in backend_modules():
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders += [
                f"{path.relative_to(PACKAGE)}: {n}" for n in names if n.startswith(FORBIDDEN)
            ]
    assert offenders == []


def test_importing_the_backend_loads_no_gui():
    code = (
        "import sys, mems_sketch, mems_sketch.cli;"
        "bad = [m for m in sys.modules if m.startswith(('PySide6', 'mems_sketch.gui'))];"
        "print(bad); sys.exit(1 if bad else 0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
