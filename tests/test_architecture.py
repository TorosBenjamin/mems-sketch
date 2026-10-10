"""The backend (everything outside mems_sketch.gui) must never depend on the GUI."""

import ast
import re
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
        "import sys, mems_sketch, mems_sketch.cli, mems_sketch.editing;"
        "bad = [m for m in sys.modules if m.startswith(('PySide6', 'mems_sketch.gui'))];"
        "print(bad); sys.exit(1 if bad else 0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


# The geometry backend: the only code that may use a geometry library's types.
# Everything else (the GUI, editing, storage, the command line) goes through
# Geometry's methods and plain values (Transform, µm tuples), so that the C++
# engine can replace the backend underneath it (core-architecture.md, step 1).
GEOMETRY_LIBRARIES = ("klayout",)
# What builds geometry is reached only through mems_sketch.engine.
BUILDERS = ("mems_sketch.core.compiler", "mems_sketch.core.shapes.render")
BUILDER_NAMES = {"Compiler", "Session", "Evaluator"}
OUTSIDE_THE_BACKEND = ("gui", "editing", "storage", "cli.py")


def test_only_the_backend_uses_a_geometry_library_or_the_compiler():
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        if path.relative_to(PACKAGE).parts[0] not in OUTSIDE_THE_BACKEND:
            continue
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [f"{node.module}.{alias.name}" for alias in node.names]
            offenders += [
                f"{path.relative_to(PACKAGE)}: {n}"
                for n in names
                if n.startswith(GEOMETRY_LIBRARIES + BUILDERS)
                or n.rsplit(".", 1)[-1] in BUILDER_NAMES
            ]
    assert offenders == []


KINDS_DIR = PACKAGE / "core" / "shapes" / "kinds"
# Checks that are about what one kind means, not a switch over every kind:
# "is this a component reference?" may be asked anywhere, and turning a
# transform into a component (the reverse of unpacking a reference) is the
# same for transforms. None means anywhere; otherwise the files allowed.
ALLOWED_CLASSES = {
    "RefShape": None,
    "TransformShape": {"editing/components.py"},
}


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


SOURCE = PACKAGE.parent
INCLUDE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.MULTILINE)


def _includes(folder: Path) -> list[tuple[Path, str]]:
    return [
        (path, header)
        for path in folder.rglob("*")
        if path.suffix in (".hpp", ".cpp", ".h")
        for header in INCLUDE.findall(path.read_text(errors="replace"))
    ]


def test_the_geometry_library_never_includes_the_engine():
    """The library stands alone (core-architecture.md): no engine header in src/geom/."""
    offenders = [
        f"{p.relative_to(SOURCE)}: {h}"
        for p, h in _includes(SOURCE / "geom")
        if h.startswith("mems/")
    ]
    assert offenders == []


ENGINE_HEADERS = ("mems", "mgeom", "nlohmann")  # its own, the library's, JSON


def test_the_engine_uses_only_the_librarys_public_headers():
    """The engine reaches Open CASCADE only through mgeom: no OCC headers, and
    only mgeom's public ones (include/mgeom/)."""
    occ = re.compile(r"^(Standard|gp|TopoDS|TopExp|BRep|Geom|BOP|TopTools|TColgp|Precision)")
    offenders = [
        f"{p.relative_to(SOURCE)}: {h}"
        for p, h in _includes(SOURCE / "engine")
        if occ.match(h)
        or (
            h.endswith(".hpp")
            and "/" in h
            and not h.startswith(".")  # its own, by relative path
            and h.split("/")[0] not in ENGINE_HEADERS
        )
    ]
    assert offenders == []
