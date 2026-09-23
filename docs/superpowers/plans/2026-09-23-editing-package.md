# Editing Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the GUI's `ProjectDocument` into a Qt-free `mems_sketch/editing/` package: an `EditSession` with command groups (`session.components`, `.nodes`, `.moves`, `.points`, `.parameters`, `.process`) and `session.results`.

**Architecture:** Code moves method by method from `gui/document.py`. A script places each method in its new class and rewrites every `self.<name>` inside it to where that name now lives. Qt signals become plain `Event`s with the same names. The GUI then holds an `EditSession` where it held a `ProjectDocument`, and its call sites are rewritten from the spec's table. Spec: `docs/superpowers/specs/2026-09-23-editing-package-design.md`.

**Tech Stack:** Python 3.11+, pydantic 2, klayout.db, PySide6 (GUI only), pytest, ruff.

**Commands** (repo root):
- Tests: `uv run --extra dev python -m pytest -q -p no:cacheprovider`
- Lint: `uv run --extra dev ruff check . && uv run --extra dev ruff format --check .`

Work in a worktree on branch `refactor/editing-package` from `development`. Baseline: 277 passed, ruff clean.

---

## Where every `ProjectDocument` member goes

```python
GROUPS = {  # old name -> (group, new name)
    "new_component": ("components", "new"),
    "delete_component": ("components", "delete"),
    "rename_component": ("components", "rename"),
    "set_top": ("components", "set_top"),
    "make_component": ("components", "make"),
    "unpack": ("components", "unpack"),
    "_check_component_name": ("components", "_check_name"),
    "add_shape": ("nodes", "add"),
    "add_primitive": ("nodes", "add_primitive"),
    "add_component": ("nodes", "add_component"),
    "replace_node": ("nodes", "replace"),
    "remove_nodes": ("nodes", "remove"),
    "duplicate": ("nodes", "duplicate"),
    "set_enabled": ("nodes", "set_enabled"),
    "set_align": ("nodes", "set_align"),
    "wrap": ("nodes", "wrap"),
    "unwrap": ("nodes", "unwrap"),
    "_siblings": ("nodes", "siblings"),
    "drag_plan": ("moves", "plan_drag"),
    "move": ("moves", "move"),
    "rotate": ("moves", "rotate"),
    "mirror": ("moves", "mirror"),
    "transform_nodes": ("moves", "transform"),
    "add_point": ("points", "add"),
    "update_point": ("points", "update"),
    "remove_point": ("points", "remove"),
    "set_parameter": ("parameters", "set"),
    "update_parameter": ("parameters", "update"),
    "add_parameter": ("parameters", "add"),
    "remove_parameter": ("parameters", "remove"),
    "set_constant": ("process", "set_constant"),
    "rename_constant": ("process", "rename_constant"),
    "remove_constant": ("process", "remove_constant"),
    "add_constant": ("process", "add_constant"),
    "set_layer": ("process", "set_layer"),
    "add_layer": ("process", "add_layer"),
    "remove_layer": ("process", "remove_layer"),
}
RESULTS = {
    "scope",
    "inspection",
    "geometry",
    "check",
    "node_regions",
    "highlight",
    "node_points",
    "align_targets",
    "declared_points",
    "all_points",
    "selection_center",
    "_inspection",  # the cache
}
# Session members that get a new name (used from the groups, or clashing with them).
RENAMED = {
    "_local": "local",
    "_shapes_in": "shapes_in",
    "_trials": "trials_for",
    "session": "compiled",
}
```

Everything else stays on the session under its own name. Module-level items:
`UNDO_LIMIT`, `new_project` → `session.py`; `DragPlan`, `_reoriented`, `_angle`,
`_round_um`, `_outermost`, `_subtree_names` → `moves.py`; `_NOT_EXPRESSIONS`,
`_parameter_values`, `_as_expression`, `_names_used` → `components.py`;
`_fresh` → `naming.py` as `fresh_name`; `VIEW_MODES` stays in the GUI (Task 2).

---

### Task 1: The editing package

**Files:**
- Create: `src/mems_sketch/editing/{__init__,events,commands,naming,session,results,components,nodes,moves,points,parameters,process}.py`
- Create: `tests/test_editing.py`

- [ ] **Step 1: Write `events.py` and `commands.py` by hand**

`src/mems_sketch/editing/events.py`:

```python
"""Plain-Python events, used where the GUI once had Qt signals."""

from __future__ import annotations

import sys
from collections.abc import Callable


class Event:
    """Callbacks run in the order they were connected, like a Qt signal with direct
    connections. A callback that raises is reported through ``sys.excepthook`` and
    the others still run, so a failing listener cannot undo what emitted the event.
    """

    def __init__(self) -> None:
        self._slots: list[Callable[..., object]] = []

    def connect(self, slot: Callable[..., object]) -> None:
        self._slots.append(slot)

    def disconnect(self, slot: Callable[..., object]) -> None:
        self._slots.remove(slot)

    def emit(self, *args: object) -> None:
        for slot in list(self._slots):
            try:
                slot(*args)
            except Exception:  # noqa: BLE001 - reported, like Qt does
                sys.excepthook(*sys.exc_info())
```

`src/mems_sketch/editing/commands.py`:

```python
"""The base class of an :class:`~mems_sketch.editing.session.EditSession`'s command groups."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mems_sketch.editing.session import EditSession


class Commands:
    """A group of edit commands; each runs as one transaction (``self.session.edit``)."""

    def __init__(self, session: EditSession) -> None:
        self.session = session
```

- [ ] **Step 2: Generate the rest with a script**

Save as `$SCRATCH/split_document.py` and run from the repo root with `uv run python`:

```python
import ast
import re
from pathlib import Path

SRC = Path("src/mems_sketch/gui/document.py")
OUT = Path("src/mems_sketch/editing")
source = SRC.read_text()
tree = ast.parse(source)
lines = source.splitlines(keepends=True)

# GROUPS, RESULTS, RENAMED: copy them from "Where every ProjectDocument member goes".
GROUPS = {...}
RESULTS = {...}
RENAMED = {...}


def segment(node) -> str:
    starts = [node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])]
    return "".join(lines[min(starts) - 1 : node.end_lineno])


def place(attr: str) -> tuple[str, str]:
    if attr in GROUPS:
        return GROUPS[attr]
    if attr in RESULTS:
        return "results", attr
    return "session", RENAMED.get(attr, attr)


def rewrite(text: str, where: str) -> str:
    """``self.<name>`` as seen from a method now living in ``where``."""

    def repl(match: re.Match) -> str:
        loc, name = place(match.group(1))
        if loc == where:
            return f"self.{name}"
        if loc == "session":
            return f"self.session.{name}"
        if where == "session":
            return f"self.{loc}.{name}"
        return f"self.session.{loc}.{name}"

    text = re.sub(r"\bself\.([A-Za-z_]\w*)", repl, text)
    return re.sub(r"\b_fresh\(", "fresh_name(", text)


cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ProjectDocument")
methods: dict[str, list[str]] = {}  # destination -> method sources
for node in cls.body:
    if not isinstance(node, ast.FunctionDef) or node.name == "__init__":
        continue
    where, name = place(node.name)
    text = segment(node)
    if name != node.name:
        text = text.replace(f"def {node.name}(", f"def {name}(", 1)
    methods.setdefault(where, []).append(rewrite(text, where))

top = {
    getattr(n, "name", None) or n.targets[0].id: n
    for n in tree.body
    if isinstance(n, ast.FunctionDef | ast.ClassDef | ast.Assign)
}


def items(*names: str) -> str:
    return "\n\n".join(rewrite(segment(top[n]), "module") for n in names)


imports = "".join(
    segment(n)
    for n in tree.body
    if isinstance(n, ast.Import | ast.ImportFrom)
    and "PySide6" not in segment(n)
    and n.module != "__future__"
)
HEADER = "from __future__ import annotations\n\n" + imports
TYPES = "\nif TYPE_CHECKING:\n    from mems_sketch.editing.session import EditSession\n"


def group(module: str, cls_name: str, doc: str, attr: str, extra: str = "", tail: str = "") -> None:
    body = "\n".join(methods[attr])
    (OUT / f"{module}.py").write_text(
        f'"""{doc}"""\n\n'
        + HEADER.replace("from typing import Any", "from typing import TYPE_CHECKING, Any")
        + "\nfrom mems_sketch.editing.commands import Commands\n"
        + "from mems_sketch.editing.naming import fresh_name\n"
        + TYPES
        + extra
        + f'\n\nclass {cls_name}(Commands):\n    """{doc}"""\n\n'
        + body
        + tail
    )


OUT.mkdir()
(OUT / "naming.py").write_text(
    '"""Names for new things: shapes, points, parameters, constants, layers."""\n\n'
    "import re\n\n\n" + segment(top["_fresh"]).replace("def _fresh(", "def fresh_name(")
)
group(
    "components",
    "ComponentEdits",
    "Creating, renaming, deleting, making and unpacking components.",
    "components",
    tail="\n\n"
    + items("_NOT_EXPRESSIONS")
    + "\n\n\n"
    + items("_parameter_values", "_as_expression", "_names_used")
    + "\n",
)
group(
    "nodes",
    "NodeEdits",
    "Adding, replacing, removing, wrapping and aligning shapes of the active component.",
    "nodes",
)
group(
    "moves",
    "MoveEdits",
    "Moving, rotating and mirroring shapes of the active component.",
    "moves",
    extra="\n\n" + items("DragPlan") + "\n",
    tail="\n\n"
    + items("_reoriented", "_angle", "_round_um", "_outermost", "_subtree_names")
    + "\n",
)
group("points", "PointEdits", "The declared points of the active component.", "points")
group("parameters", "ParameterEdits", "The parameters of the active component.", "parameters")
group("process", "ProcessEdits", "Process constants and layers.", "process")
(OUT / "results.py").write_text(
    '"""What the components evaluate to: geometry, rule checks, node positions and points."""\n\n'
    + HEADER.replace("from typing import Any", "from typing import TYPE_CHECKING, Any")
    + TYPES
    + '\n\nclass Results:\n    """What a session\'s components evaluate to, computed on demand and cached until\n'
    '    the session changes. Of the active component unless another is named."""\n\n'
    "    def __init__(self, session: EditSession) -> None:\n"
    "        self.session = session\n"
    "        self._inspection: dict[str, dict[NodePath, NodeRecord]] = {}\n"
    "        session.changed.connect(self._inspection.clear)\n\n" + "\n".join(methods["results"])
)
(OUT / "session.py").write_text(
    ast.get_docstring(tree)
    .replace(
        "The open project, as the GUI sees it: the frontend's only way into the backend.",
        "An open project being edited: what every frontend edits through.",
    )
    .join(['"""', '\n"""\n\n'])
    + HEADER
    + "\nfrom mems_sketch.editing.components import ComponentEdits\n"
    "from mems_sketch.editing.events import Event\n"
    "from mems_sketch.editing.moves import MoveEdits\n"
    "from mems_sketch.editing.naming import fresh_name\n"
    "from mems_sketch.editing.nodes import NodeEdits\n"
    "from mems_sketch.editing.parameters import ParameterEdits\n"
    "from mems_sketch.editing.points import PointEdits\n"
    "from mems_sketch.editing.process import ProcessEdits\n"
    "from mems_sketch.editing.results import Results\n\n"
    + items("UNDO_LIMIT")
    + "\n\n\n"
    + items("new_project")
    + "\n\n\n"
    + SESSION_CLASS_HEAD
    + "\n".join(methods["session"])
)
```

`SESSION_CLASS_HEAD` (define it in the script before use):

```python
SESSION_CLASS_HEAD = '''class EditSession:
    """An open project being edited; see the module docstring."""

    def __init__(self, project: Project | None = None) -> None:
        self.changed = Event()  # the model changed (or trial values); views must refresh
        self.active_changed = Event()  # another component became active; nothing else changed
        self.file_changed = Event()  # path or dirty flag changed
        self.component_renamed = Event()  # old, new
        self.project = project or new_project()
        self.active = self.project.top
        self.path: Path | None = None
        self.dirty = False
        self.compiler = Compiler()
        # (description, other project state, active before the change, active after it)
        self._undo: list[tuple[str, Project, str, str]] = []
        self._redo: list[tuple[str, Project, str, str]] = []
        self.trials: dict[str, dict[str, Value]] = {}
        self._saved: dict[str, str] = self._fingerprints()
        self.results = Results(self)  # connects first: its cache clears before views refresh
        self.components = ComponentEdits(self)
        self.nodes = NodeEdits(self)
        self.moves = MoveEdits(self)
        self.points = PointEdits(self)
        self.parameters = ParameterEdits(self)
        self.process = ProcessEdits(self)

    @classmethod
    def open_project(cls, path: str | Path) -> EditSession:
        """A session on the project at ``path``: a folder, its project.yaml or a .mems file."""
        session = cls()
        session.open(path)
        return session

'''
```

Then write `src/mems_sketch/editing/__init__.py`:

```python
"""Editing projects: the backend every frontend (GUI, CLI, scripts) edits through.

session = EditSession.open_project("examples/resonator")
session.set_active("suspension")
session.components.make([((0, 0),), ((0, 1),)], "spring_with_anchor")
session.save()
"""

from mems_sketch.editing.events import Event
from mems_sketch.editing.moves import DragPlan
from mems_sketch.editing.session import EditSession, new_project

__all__ = ["DragPlan", "EditSession", "Event", "new_project"]
```

- [ ] **Step 3: Tidy and check nothing was lost**

```bash
uv run --extra dev ruff check --fix src/mems_sketch/editing && uv run --extra dev ruff format src/mems_sketch/editing
uv run --extra dev ruff check .
```
Unused imports go (every module starts from the full import list). Fix any undefined name (F821) by importing it. Then check that every `ProjectDocument` member and module-level item exists exactly once in the package (under its new name), with an AST comparison like the one in part 1.

Then confirm no `self.<name>` points at a member that no longer exists where it is used: `grep -n "self\.session\.\(results\|components\|nodes\|moves\|points\|parameters\|process\)\." src/mems_sketch/editing/*.py` must only show names that exist in that group.

- [ ] **Step 4: Tests for the package, without Qt**

`tests/test_editing.py` is `tests/test_gui_document.py` with:
- `pytest.importorskip("PySide6")` removed, `from mems_sketch.gui.document import ProjectDocument` → `from mems_sketch.editing import EditSession`, every `ProjectDocument` → `EditSession`, the fixture `def doc(qapp)` → `def doc()`;
- calls rewritten by the table: apply `rewrite_calls` (Task 2, Step 1) to the file.

Run: `uv run --extra dev python -m pytest -q -p no:cacheprovider tests/test_editing.py`
Expected: the same number of tests as `tests/test_gui_document.py`, all passing.

Run the full suite: expected all passing (277 + the copied tests). Lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/mems_sketch/editing tests/test_editing.py
git commit -m "Editing as a Qt-free backend package (refactor 2, 1/3)"
```

---

### Task 2: The GUI uses the editing package

**Files:**
- Modify: `src/mems_sketch/gui/{app,panels,properties,tools,views}.py`, `tests/test_gui_*.py`
- Delete: `src/mems_sketch/gui/document.py`, `tests/test_gui_document.py`

- [ ] **Step 1: Rewrite the call sites**

Save as `$SCRATCH/rewrite_calls.py` (with `GROUPS` and `RESULTS` from the table above), run on the GUI modules and the GUI tests:

```python
import re
import sys
from pathlib import Path

GROUPS = {...}
RESULTS = {...}


def rewrite_calls(text: str) -> str:
    def repl(match: re.Match) -> str:
        owner, name = match.groups()
        if name in GROUPS:
            group, new = GROUPS[name]
            return f"{owner}.{group}.{new}"
        return f"{owner}.results.{name}"

    names = "|".join(sorted((set(GROUPS) | RESULTS) - {"_inspection"}, key=len, reverse=True))
    return re.sub(rf"\b(document|doc)\.({names})\b", repl, text)


for path in map(Path, sys.argv[1:]):
    path.write_text(rewrite_calls(path.read_text()))
```

```bash
uv run python $SCRATCH/rewrite_calls.py src/mems_sketch/gui/*.py tests/test_gui_*.py
```

(`src/mems_sketch/gui/document.py` is rewritten too; it is deleted in Step 3.)

- [ ] **Step 2: Imports and names**

- `ProjectDocument` → `EditSession` everywhere in `gui/` and the GUI tests; `from mems_sketch.gui.document import ProjectDocument` → `from mems_sketch.editing import EditSession`.
- `tools.py`: `from mems_sketch.gui.document import DragPlan` → `from mems_sketch.editing import DragPlan`.
- Move `VIEW_MODES` from `gui/document.py` to `gui/views.py`; in `app.py` import it from there.

- [ ] **Step 3: Delete the old module and its tests**

```bash
git rm -q src/mems_sketch/gui/document.py tests/test_gui_document.py
```

- [ ] **Step 4: Check nothing still uses an old name**

```bash
grep -rnE "\b(document|doc)\.(new_component|delete_component|rename_component|make_component|add_shape|replace_node|remove_nodes|drag_plan|transform_nodes|add_point|update_point|remove_point|set_parameter|update_parameter|add_parameter|remove_parameter|highlight|inspection|node_points|align_targets|declared_points|all_points|selection_center|node_regions)\b" src tests
grep -rn "ProjectDocument\|gui.document" src tests
```
Expected: no output.

- [ ] **Step 5: Full suite and lint, commit**

Expected: all passing (same count as after Task 1 minus the deleted `test_gui_document.py` tests); ruff clean.

```bash
git add -A src/mems_sketch/gui tests
git commit -m "GUI edits through the editing package; ProjectDocument is gone (refactor 2, 2/3)"
```

---

### Task 3: Guard rails and docs

**Files:**
- Modify: `tests/test_architecture.py`, `tests/test_editing.py`, `README.md`

- [ ] **Step 1: Importing the editing package loads no Qt**

In `test_importing_the_backend_loads_no_gui`, import it too:

```python
        "import sys, mems_sketch, mems_sketch.cli, mems_sketch.editing;"
```

- [ ] **Step 2: Event and script tests**

Append to `tests/test_editing.py` (add `import shutil`, `from pathlib import Path`, and `Event` to the editing import):

```python
EXAMPLES = Path(__file__).parent.parent / "examples"


def test_events_call_slots_in_order_and_survive_a_failing_one(monkeypatch):
    reported = []
    monkeypatch.setattr("sys.excepthook", lambda *exc: reported.append(exc[0]))
    event, calls = Event(), []

    def fail(*args):
        raise RuntimeError("listener broke")

    event.connect(lambda *a: calls.append(("first", a)))
    event.connect(fail)
    event.connect(lambda *a: calls.append(("last", a)))
    event.emit("old", "new")
    assert calls == [("first", ("old", "new")), ("last", ("old", "new"))]
    assert reported == [RuntimeError]
    event.disconnect(fail)
    event.emit()
    assert reported == [RuntimeError]


def test_a_script_can_edit_and_save_a_project(tmp_path):
    for name in ("resonator", "libraries"):
        shutil.copytree(
            EXAMPLES / name, tmp_path / name, ignore=shutil.ignore_patterns(".mems-sketch")
        )
    session = EditSession.open_project(tmp_path / "resonator")
    session.set_active("suspension")
    before = session.results.geometry()
    session.components.make([((0, 0),), ((0, 1),)], "spring_with_anchor")
    session.save()

    again = EditSession.open_project(tmp_path / "resonator")
    assert "spring_with_anchor" in again.project.components
    after = again.results.geometry(component="suspension")
    assert before.layers.keys() == after.layers.keys()
    assert all((before.layers[k] ^ after.layers[k]).is_empty() for k in before.layers)
```

Run: `uv run --extra dev python -m pytest -q -p no:cacheprovider tests/test_editing.py tests/test_architecture.py` — expected all passing.

- [ ] **Step 3: README**

In the README's architecture section, after the "Frontends" bullet, add:

```markdown
- **Editing:** `mems_sketch.editing` is how every frontend changes a project:
  an `EditSession` with undo, and command groups for components, shapes,
  moves, points, parameters and the process. The GUI is one user; a script
  is another:

  ```python
  from mems_sketch.editing import EditSession

  session = EditSession.open_project("examples/resonator")
  session.set_active("suspension")
  session.components.make([((0, 0),), ((0, 1),)], "spring_with_anchor")
  session.save()
  ```
```

- [ ] **Step 4: Full suite and lint, commit**

```bash
git add tests/test_architecture.py tests/test_editing.py README.md
git commit -m "Test the editing package on its own, and document editing from scripts (refactor 2, 3/3)"
```
