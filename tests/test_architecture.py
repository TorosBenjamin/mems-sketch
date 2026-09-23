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


KINDS_DIR = PACKAGE / "core" / "shapes" / "kinds"
# Checks that are about what one kind means, not a switch over every kind:
# "is this a component reference?" may be asked anywhere, and turning a
# transform into a component (the reverse of unpacking a reference) is the
# same for transforms. None means anywhere; otherwise the files allowed.
ALLOWED_CLASSES = {"RefShape": None, "TransformShape": {"gui/document.py"}}


def _class_names(node) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Tuple):
        return [name for element in node.elts for name in _class_names(element)]
    if isinstance(node, ast.BinOp):
        return _class_names(node.left) + _class_names(node.right)
    return []


def _allowed(name: str, path: Path) -> bool:
    if name not in ALLOWED_CLASSES:
        return False
    files = ALLOWED_CLASSES[name]
    return files is None or path.relative_to(PACKAGE).as_posix() in files


def kind_switches(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        where = f"{path.relative_to(PACKAGE)}:{getattr(node, 'lineno', '?')}"
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
            and len(node.args) == 2
        ):
            names = [
                n
                for n in _class_names(node.args[1])
                if n.endswith("Shape") and not _allowed(n, path)
            ]
            if names:
                found.append(f"{where}: isinstance(…, {' | '.join(names)})")
        elif isinstance(node, ast.MatchClass) and isinstance(node.cls, ast.Name):
            if node.cls.id.endswith("Shape"):
                found.append(f"{where}: case {node.cls.id}()")
        elif isinstance(node, ast.Compare) and isinstance(node.left, ast.Attribute):
            if node.left.attr == "kind":
                found.append(f"{where}: compares .kind")
        elif isinstance(node, ast.Match) and isinstance(node.subject, ast.Attribute):
            if node.subject.attr == "kind":
                found.append(f"{where}: match on .kind")
    return found


def test_only_the_shape_kinds_switch_on_kinds():
    """Kind-specific behaviour lives in core/shapes/kinds; elsewhere use the Node methods."""
    offenders = [
        switch
        for path in PACKAGE.rglob("*.py")
        if KINDS_DIR not in path.parents
        for switch in kind_switches(path)
    ]
    assert offenders == []
