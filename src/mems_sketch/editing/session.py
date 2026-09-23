"""An open project being edited: what every frontend edits through.

Frontends (the GUI, the CLI, scripts) never build geometry or touch files
themselves; they go through a session, which uses the rest of the backend
(project model, compiler, storage, rules, export). Edits are grouped:
``session.components``, ``.nodes``, ``.moves``, ``.points``, ``.parameters``
and ``.process``; ``session.results`` is what the components evaluate to.

* One component is **active**, e.g. the one in the GUI's current tab. Shape edits,
  parameters and points refer to it. Any component can be active; library
  and built-in components are read-only.
* **Trial values** per component override parameter defaults for viewing
  only: they are not part of the project, not saved and not undone.
* Every edit runs as a transaction: the change is applied, the project is
  recompiled, and if a project that compiled before no longer does, the change
  is rolled back exactly.
* Undo/redo keep whole-project snapshots (libraries are read-only and shared)
  and remember which component each change was made in, so a frontend can
  go back to it, like a code editor.
* A long-lived :class:`Compiler` caches built components by fingerprint, so
  recompiling after an edit only rebuilds what changed.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mems_sketch.core.compiler import Compiler, Session
from mems_sketch.core.component import Component
from mems_sketch.core.project import Project, new_project
from mems_sketch.core.shapes import (
    NodePath,
    RefShape,
    Shape,
    Value,
    node_at,
    walk,
)
from mems_sketch.core.user_component import ComponentDef, ParamDef
from mems_sketch.editing.components import ComponentEdits
from mems_sketch.editing.events import Event
from mems_sketch.editing.moves import MoveEdits
from mems_sketch.editing.naming import fresh_name
from mems_sketch.editing.nodes import NodeEdits
from mems_sketch.editing.parameters import ParameterEdits
from mems_sketch.editing.points import PointEdits
from mems_sketch.editing.process import ProcessEdits
from mems_sketch.editing.results import Results
from mems_sketch.export.base import export
from mems_sketch.storage import load, save
from mems_sketch.storage.project_files import project_folder

UNDO_LIMIT = 200


class EditSession:
    """An open project being edited; see the module docstring."""

    def __init__(self, project: Project | None = None) -> None:
        self.changed = Event()  # the model changed (or trial values); views must refresh
        self.active_changed = Event()  # another component became active; nothing else changed
        self.file_changed = Event()  # path or dirty flag changed
        self.component_renamed = Event()  # old, new
        self.project = project or new_project()
        self.active = self.project.default_component()
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

    def edit(
        self,
        description: str,
        change: Callable[[Project], object],
        done: Callable[[], None] | None = None,
    ) -> object:
        """Apply ``change``; roll back and re-raise if a valid project would become invalid.

        ``done`` runs after a successful change, before views are told.
        """
        before, active = self._snapshot(), self.active
        was_valid = not self.problems(before)
        try:
            result = change(self.project)
            if was_valid:
                problems = self.problems()
                if problems:
                    raise ValueError(problems[0])
        except Exception:
            self.project, self.active = before, active
            raise
        if not self.exists(self.active):
            self.active = self.project.default_component()
        self._undo.append((description, before, active, self.active))
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()
        if done is not None:
            done()
        self._set_dirty(True)
        self.changed.emit()
        return result

    def _snapshot(self) -> Project:
        # Libraries are read-only: share them instead of copying (the dict is
        # copied, so adding or removing a library can be undone).
        shared = {id(lib): lib for lib in self.project.libraries.values()}
        return copy.deepcopy(self.project, shared)

    def problems(self, project: Project | None = None) -> list[str]:
        """Why the project does not compile (empty when it does)."""
        project = project or self.project
        try:
            project.check_references()
        except ValueError as exc:
            return [str(exc)]
        session = self.compiler.session(project)
        problems = []
        for name in project.components:
            try:
                session.render(name)
            except Exception as exc:  # noqa: BLE001 - collected for the caller
                problems.append(f"{name}: {exc}")
        return problems

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_text(self) -> str:
        return self._undo[-1][0] if self._undo else ""

    def redo_text(self) -> str:
        return self._redo[-1][0] if self._redo else ""

    def undo(self) -> None:
        """Undo the last change and make the component it was made in active."""
        if self._undo:
            description, state, before, after = self._undo.pop()
            self._redo.append((description, self.project, before, after))
            self._restore(state, before)

    def redo(self) -> None:
        if self._redo:
            description, state, before, after = self._redo.pop()
            self._undo.append((description, self.project, before, after))
            self._restore(state, after)

    def _restore(self, project: Project, active: str) -> None:
        self.project = project
        self.active = active if self.exists(active) else project.default_component()
        self._set_dirty(self._fingerprints() != self._saved)
        self.changed.emit()

    def new(self, library: bool = False) -> None:
        """Start a new design, or a new library (no top component)."""
        self._reset(new_project(library=library), None)

    def open(self, path: str | Path) -> None:
        """Open a project folder, its project.yaml, or a legacy .mems file."""
        path = Path(path)
        project = load(path)
        if path.suffix == ".mems":  # legacy import: must be saved as a project folder
            self._reset(project, None)
            self._set_dirty(True)
        else:
            self._reset(project, project_folder(path))

    def save(self, folder: str | Path | None = None) -> Path:
        target = Path(folder) if folder is not None else self.path
        if target is None:
            raise ValueError("choose a folder to save the project in")
        save(self.project, target)
        self.path = target
        self._saved = self._fingerprints()
        self._set_dirty(False)
        self.changed.emit()  # tabs drop their "modified" marks
        return target

    def export(self, path: str | Path, mode: str = "drawn") -> Path:
        return export(self.project, path, geometry=self.results.geometry(mode))

    def _reset(self, project: Project, path: Path | None) -> None:
        self.project = project
        self.active = project.default_component()
        self.path = path
        self._undo.clear()
        self._redo.clear()
        self.trials.clear()
        self._saved = self._fingerprints()
        self._set_dirty(False)
        self.changed.emit()

    def _fingerprints(self) -> dict[str, str]:
        return {n: d.model_dump_json() for n, d in self.project.components.items()}

    def modified(self, component: str) -> bool:
        """Whether a local component differs from the saved (or opened) project."""
        if component not in self.project.components:
            return False
        return self._saved.get(component) != self.project.components[component].model_dump_json()

    def _set_dirty(self, dirty: bool) -> None:
        self.dirty = dirty
        self.file_changed.emit()

    def compiled(self) -> Session:
        return self.compiler.session(self.project)

    def exists(self, component: str) -> bool:
        """Whether ``component`` (as written at project level) can be opened."""
        try:
            self.project.qualify(component)
            return True
        except KeyError:
            return False

    @property
    def read_only(self) -> bool:
        """Library and built-in components can be viewed but not edited."""
        return self.active not in self.project.components

    def definition_of(self, component: str) -> ComponentDef:
        """A component's definition; for a built-in, one listing its numeric parameters."""
        found = self.project.definition(self.project.qualify(component))
        if found is not None:
            return found[0]
        schema = self.compiled().component(component).Params.model_fields
        return ComponentDef(
            name=component,
            parameters=[
                ParamDef(name=name, default=info.default, description=info.description or "")
                for name, info in schema.items()
                if info.annotation in (int, float)
            ],
        )

    @property
    def shapes(self) -> list[Shape]:
        return self.active_definition.shapes

    @property
    def active_definition(self) -> ComponentDef:
        return self.definition_of(self.active)

    def local(self, project: Project) -> ComponentDef:
        """The active component inside ``project``, for an edit; refuses read-only ones."""
        if self.active not in project.components:
            raise ValueError(
                f"'{self.active}' is read-only: library and built-in components cannot be "
                "edited here"
            )
        return project.components[self.active]

    def set_trial(self, name: str, value: Value | None, component: str | None = None) -> None:
        """Try a parameter value for viewing only (None goes back to the default)."""
        component = component or self.active
        trials = self.trials.setdefault(component, {})
        if value is None:
            trials.pop(name, None)
        else:
            previous = trials.get(name)
            trials[name] = value
            try:
                self.compiled().variables(component, self.trials_for(component))
            except Exception:
                if previous is None:
                    trials.pop(name)
                else:
                    trials[name] = previous
                raise
        self.changed.emit()

    def restore_trials(self, trials: dict[str, dict[str, Value]]) -> None:
        """Take over saved trial values, quietly dropping ones that no longer apply."""
        self.trials = {}
        for component, values in trials.items():
            if not self.exists(component) or not isinstance(values, dict):
                continue
            for name, value in values.items():
                kept = self.trials.setdefault(component, {})
                kept[name] = value
                try:
                    self.compiled().variables(component, self.trials_for(component))
                except Exception:  # noqa: BLE001 - an outdated trial value is dropped
                    kept.pop(name)

    def trials_for(self, component: str) -> dict[str, Value]:
        """Trial values that still apply (parameters may have been renamed or removed)."""
        trials = self.trials.get(component)
        if not trials:
            return {}
        try:
            schema = self.compiled().component(component).Params.model_fields
        except KeyError:
            return {}
        return {k: v for k, v in trials.items() if k in schema}

    def component_names(self) -> list[str]:
        return self.project.component_names()

    def component(self, name: str) -> Component:
        return self.compiled().component(name)

    def parameter_defaults(self, component: str) -> dict[str, Any]:
        """Declared defaults (numbers or expressions) of a component's parameters."""
        built = self.component(component)
        definition = getattr(built, "definition", None)
        if definition is not None:
            return {p.name: p.default for p in definition.parameters}
        return {name: info.default for name, info in built.Params.model_fields.items()}

    def node(self, path: NodePath) -> Shape:
        return node_at(self.shapes, path)

    def unique_name(self, stem: str) -> str:
        return fresh_name(stem, {s.name for s in walk(self.shapes) if s.name})

    def reference_target(self, path: NodePath, component: str | None = None) -> str | None:
        """The component a ``ref`` node places, named as the project sees it, else None."""
        component = component or self.active
        node = node_at(self.definition_of(component).shapes, path)
        if not isinstance(node, RefShape):
            return None
        namespace = component.partition(".")[0] if "." in component else None
        return self.project.qualify(node.component, namespace)

    def set_active(self, name: str) -> None:
        """Make a component active: a local one to edit, or a library/built-in one to view."""
        if not self.exists(name):
            raise ValueError(f"unknown component '{name}'")
        if name != self.active:
            self.active = name
            self.active_changed.emit()

    def shapes_in(self, project: Project) -> list[Shape]:
        return self.local(project).shapes
