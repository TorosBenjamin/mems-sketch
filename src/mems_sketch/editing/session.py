"""An open project being edited: what every frontend edits through.

Frontends (the GUI, the CLI, scripts) never build geometry or touch files
themselves; they go through a session, which uses the rest of the backend
(project model, engine, storage, rules, export). Edits are grouped:
``session.components``, ``.nodes``, ``.modifiers``, ``.moves``, ``.points``,
``.ports``, ``.parameters`` and ``.process``; ``session.results`` is what the components evaluate to.

* One component is **active**, e.g. the one in the GUI's current tab. Shape edits,
  parameters and points refer to it. Any component can be active; library
  components are read-only.
* **Trial values** per component override parameter defaults for viewing
  only: they are not part of the project, not saved and not undone.
* Every edit runs as a transaction: the change is applied, the project is
  recompiled, and if a project that compiled before no longer does, the change
  is rolled back exactly.
* Undo/redo keep whole-project snapshots (libraries are read-only and shared)
  and remember which component each change was made in, so a frontend can
  go back to it, like a code editor.
* A long-lived :class:`~mems_sketch.engine.Engine` caches built components by
  fingerprint, so recompiling after an edit only rebuilds what changed.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from mems_sketch.core.component import Component
from mems_sketch.core.project import MAIN_PROCESS, Project, new_project
from mems_sketch.core.shapes import (
    NodePath,
    RefShape,
    Shape,
    Value,
    node_at,
    walk,
)
from mems_sketch.core.user_component import ComponentDef, ParamDef, name_shapes
from mems_sketch.editing.clipboard import ClipboardEdits
from mems_sketch.editing.components import ComponentEdits, library_name
from mems_sketch.editing.corners import CornerEdits
from mems_sketch.editing.dimensions import Dimensions
from mems_sketch.editing.events import Event
from mems_sketch.editing.handles import HandleEdits
from mems_sketch.editing.history import History
from mems_sketch.editing.imports import ImportEdits
from mems_sketch.editing.modifiers import ModifierEdits
from mems_sketch.editing.moves import MoveEdits
from mems_sketch.editing.naming import fresh_name
from mems_sketch.editing.nodes import NodeEdits
from mems_sketch.editing.parameters import ParameterEdits
from mems_sketch.editing.points import PointEdits
from mems_sketch.editing.ports import PortEdits
from mems_sketch.editing.process import ProcessEdits
from mems_sketch.editing.results import Results
from mems_sketch.engine import Engine
from mems_sketch.export.base import export_with_report
from mems_sketch.storage import is_copy, is_document, load, save
from mems_sketch.storage.project_files import is_project_folder, load_library, project_folder

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
        self._engine = Engine()
        # Whether an edit is refused when the project would no longer build. Building
        # a big design takes seconds, so the editor turns this off for one and shows
        # what does not build instead (references are checked either way).
        self.check_builds = True
        # A value is being dragged (QS-9): the design follows each step, and the
        # editor may leave the rule checks until the drag ends.
        self.dragging = False
        self._valid: bool | None = None  # whether the project builds, once known
        # Edits made without that check since the last time someone looked (see
        # :meth:`take_back_unchecked`): the editor builds afterwards and takes them back
        # if they broke a project that built.
        self.unchecked_edits = 0
        # (description, other project state, active before the change, active after it)
        # Each step: what it was, the project before it, the active component
        # before and after it, and the components it renamed (old: new).
        self._undo: list[tuple[str, Project, str, str, dict[str, str]]] = []
        self._redo: list[tuple[str, Project, str, str]] = []
        self.trials: dict[str, dict[str, Value]] = {}
        self._saved: dict[str, str] = self._fingerprints()
        self.results = Results(self)  # connects first: its cache clears before views refresh
        self.components = ComponentEdits(self)
        self.nodes = NodeEdits(self)
        self.clipboard = ClipboardEdits(self)
        self.modifiers = ModifierEdits(self)
        self.corners = CornerEdits(self)
        self.imports = ImportEdits(self)
        self.moves = MoveEdits(self)
        self.handles = HandleEdits(self)  # dragging a shape's sides and vertices
        self.dimensions = Dimensions(self)  # parameters on the canvas
        self.points = PointEdits(self)
        self.ports = PortEdits(self)
        self.parameters = ParameterEdits(self)
        self.process = ProcessEdits(self)
        self.history = History(self)

    @classmethod
    def open_project(cls, path: str | Path) -> EditSession:
        """A session on the project at ``path``: a folder, its project file or a .mems file."""
        session = cls()
        session.open(path)
        return session

    def edit(
        self,
        description: str,
        change: Callable[[Project], object],
        done: Callable[[], None] | None = None,
        renames: dict[str, str] | None = None,
    ) -> object:
        """Apply ``change``; roll back and re-raise if a valid project would become invalid
        (with :attr:`check_builds`), or if its references would stop resolving or form a
        cycle (a component placing itself cannot be drawn, so that is refused even in a
        project with problems).

        ``done`` runs after a successful change, before views are told.
        ``renames`` (old: new) are the components it renames: undoing it says they
        are renamed back, so what shows them (a tab) follows instead of closing.
        """
        before, active = self._snapshot(), self.active
        was_valid = self.check_builds and self._builds(before)
        references_valid = was_valid or _references_valid(before)
        try:
            result = change(self.project)
            for definition in self.project.components.values():
                name_shapes(definition.shapes)  # every shape has a name (PRJ-3)
            if was_valid:
                problems = self.problems()
                if problems:
                    raise ValueError(problems[0])
            elif references_valid:
                self.project.check_references()
        except Exception:
            self.project, self.active = before, active
            raise
        self._valid = True if was_valid else None
        if not self.check_builds:
            self.unchecked_edits += 1
        if not self.exists(self.active):
            self.active = self.project.default_component()
        self._undo.append((description, before, active, self.active, dict(renames or {})))
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()
        if done is not None:
            done()
        self._set_dirty(True)
        self.changed.emit()
        return result

    def _builds(self, project: Project) -> bool:
        """Whether ``project`` (the session's, as it is) builds; known after an edit."""
        if self._valid is None:
            self._valid = not self.problems(project)
        return self._valid

    def _snapshot(self) -> Project:
        # Libraries are read-only: share them instead of copying (the dict is
        # copied, so adding or removing a library can be undone).
        shared = {id(lib): lib for lib in self.project.libraries.values()}
        return copy.deepcopy(self.project, shared)

    def problems(self, project: Project | None = None) -> list[str]:
        """Why the project does not compile (empty when it does)."""
        return self._engine.trial(project or self.project).problems()

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
            description, state, before, after, renames = self._undo.pop()
            self._redo.append((description, self.project, before, after, renames))
            self._restore(state, before, {new: old for old, new in renames.items()})

    def redo(self) -> None:
        if self._redo:
            description, state, before, after, renames = self._redo.pop()
            self._undo.append((description, self.project, before, after, renames))
            self._restore(state, after, renames)

    def take_back_unchecked(self, count: int) -> None:
        """Undo the last ``count`` edits, made without the build check, that broke the
        project (the editor found out when it built them); they can be redone."""
        for _ in range(count):
            if self._undo:
                self.undo()

    def _restore(self, project: Project, active: str, renames: dict[str, str]) -> None:
        self.project, self._valid = project, None
        self.unchecked_edits = 0
        self.active = active if self.exists(active) else project.default_component()
        self._set_dirty(self._fingerprints() != self._saved)
        for old, new in renames.items():  # before views are told: a tab follows
            self.component_renamed.emit(old, new)
        self.changed.emit()

    def new(self, library: bool = False) -> None:
        """Start a new design, or a new library (no top component)."""
        self._reset(new_project(library=library), None)

    def create(
        self,
        folder: str | Path,
        name: str,
        *,
        library: bool = False,
        libraries: Sequence[str | Path] = (),
        process_from: str | Path | None = None,
        process: str | None = None,
    ) -> Path:
        """Start a new project (or library) called ``name`` and save it in ``folder``
        at once: with the ``libraries`` loaded, and a library's ``process``
        (``mems.surface``), or the process copied from the project at
        ``process_from``, else the default layers."""
        folder = Path(folder)
        if not name.strip():
            raise ValueError("the project needs a name")
        if is_project_folder(folder):
            raise ValueError(f"{folder} already holds a project")
        if folder.exists() and not folder.is_dir():
            raise ValueError(f"{folder} is a file, not a folder")
        project = new_project(name.strip(), library=library)
        if process_from is not None:  # a copy, as the new project's own process
            own = copy.deepcopy(load(process_from).process)
            project.processes, project.process = {MAIN_PROCESS: own}, own
        for path in libraries:
            key = library_name(path)
            if key in project.libraries:
                raise ValueError(f"two libraries would both be called '{key}'")
            project.libraries[key] = load_library(key, Path(path))
            if not project.libraries[key].components and not project.libraries[key].processes:
                raise ValueError(f"{path} has no components or processes")
        if process is not None:
            if process not in project.process_names() or "." not in process:
                raise ValueError(f"no library has a process '{process}'")
            project.use_process(process)
        self._reset(project, None)
        return self.save(folder)

    def open(self, path: str | Path) -> None:
        """Open a project folder, its project file (or a component's or process's
        ``.msketch`` file in it), or a legacy .mems file."""
        path = Path(path)
        project = load(path)
        if is_copy(path):  # a legacy design or a one-file document: saved as a folder
            self._reset(project, None)
            self._set_dirty(True)
        else:
            self._reset(project, project_folder(path))

    def save(self, folder: str | Path | None = None, *, replace: bool = False) -> Path:
        """Save in the project's folder, or in ``folder``: one that holds another
        project (or a one-file project that exists) only with ``replace``."""
        target = Path(folder) if folder is not None else self.path
        if target is None:
            raise ValueError("choose a folder to save the project in")
        if not is_document(target):
            target = project_folder(target)
        if not replace and _holds_another_project(target, self.path):
            raise ValueError(f"{target} already holds a project: choose another folder")
        save(self.project, target)
        self.path = target
        self._saved = self._fingerprints()
        self._set_dirty(False)
        self.changed.emit()  # tabs drop their "modified" marks
        return target

    def has_old_file_names(self) -> bool:
        """Whether the project's folder has the file names from before #139
        (``project.yaml``): :meth:`use_msketch_names` gives it the new ones."""
        from mems_sketch.storage.project_files import PROJECT_FILE

        return self.path is not None and (self.path / PROJECT_FILE).is_file()

    def use_msketch_names(self) -> list[tuple[Path, Path]]:
        """Save, then rename the project's files to ``.msketch`` names (#139):
        (old, new) for each; in git the change shows as renames to commit."""
        from mems_sketch.storage.project_files import use_msketch_names

        if self.path is None:
            raise ValueError("the project is not saved in a folder yet")
        if self.dirty:
            self.save()
        renamed = use_msketch_names(self.path)
        self.changed.emit()
        return renamed

    def export(
        self, path: str | Path, format_name: str | None = None, options: dict | None = None
    ) -> Path:
        """Export the active component with its trial values."""
        return self.export_with_report(path, format_name, options)[0]

    def export_with_report(
        self, path: str | Path, format_name: str | None = None, options: dict | None = None
    ) -> tuple[Path, Any]:
        """:meth:`export`, and what the exporter returned (for a layout file, the
        output written, which says what snapping to its grid changed)."""
        component = self.active
        build = self.results.build(component)
        return export_with_report(
            self.project,
            path,
            format_name=format_name,
            geometry=build.geometry,
            component=component,
            params=self.trials_for(component),
            options=options,
            build=build,
        )

    def _reset(self, project: Project, path: Path | None) -> None:
        self.project, self._valid = project, None
        self.unchecked_edits = 0
        self.active = project.default_component()
        self.path = path
        self._undo.clear()
        self._redo.clear()
        self.trials.clear()
        self.history.forget()
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

    @property
    def engine(self) -> Engine:
        """The engine, with the project as it is now loaded."""
        return self._engine.load(self.project)

    def exists(self, component: str) -> bool:
        """Whether ``component`` (as written at project level) can be opened. None
        (nothing to show) is what a library of processes only opens."""
        if component is None:
            return self.project.default_component() is None
        try:
            self.project.qualify(component)
            return True
        except KeyError:
            return False

    @property
    def read_only(self) -> bool:
        """Library components can be viewed but not edited."""
        return self.active not in self.project.components

    def definition_of(self, component: str) -> ComponentDef:
        """A component's definition; for an imported cell, one listing its numeric
        parameters (none). With no component (a library of processes only), an
        empty one, which cannot be edited."""
        if component is None:
            return ComponentDef.model_construct(name="", shapes=[], parameters=[])
        found = self.project.definition(self.project.qualify(component))
        if found is not None:
            return found[0]
        schema = self.engine.component(component).Params.model_fields
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
                f"'{self.active}' is read-only: library components cannot be edited here"
            )
        return project.components[self.active]

    def set_trial(self, name: str, value: Value | None, component: str | None = None) -> None:
        """Try a parameter value for viewing only (None goes back to the default)."""
        component = component or self.active
        trials = self.trials.setdefault(component, {})
        if value is None:
            trials.pop(name, None)
        else:
            target = self.engine.component(component)
            if name not in target.Params.model_fields:
                known = ", ".join(k for k in target.Params.model_fields if k not in target.internal)
                raise ValueError(
                    f"'{component}' has no parameter '{name}' (its parameters: {known})"
                )
            previous = trials.get(name)
            trials[name] = value
            try:
                self.engine.variables(component, self.trials_for(component))
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
                    self.engine.variables(component, self.trials_for(component))
                except Exception:  # noqa: BLE001 - an outdated trial value is dropped
                    kept.pop(name)

    def trials_for(self, component: str) -> dict[str, Value]:
        """Trial values that still apply (parameters may have been renamed or removed)."""
        trials = self.trials.get(component)
        if not trials:
            return {}
        try:
            schema = self.engine.component(component).Params.model_fields
        except KeyError:
            return {}
        return {k: v for k, v in trials.items() if k in schema}

    def component_names(self, component: str | None = None) -> list[str]:
        """What can be placed in a component (default: the active one), as it writes
        them; not the component itself."""
        component = component or self.active
        return [
            name
            for name in self.project.component_names(component)
            if self.project.qualify(name, component) != component
        ]

    def resolve(self, name: str, component: str | None = None) -> str:
        """The unique name of what a reference ``name`` in a component (default: the
        active one) places."""
        return self.project.qualify(name, component or self.active)

    def component(self, name: str) -> Component:
        return self.engine.component(name)

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
        return self.project.qualify(node.component, component)

    def set_active(self, name: str) -> None:
        """Make a component active: a local one to edit, or a library one to view."""
        if not self.exists(name):
            raise ValueError(f"unknown component '{name}'")
        if name != self.active:
            self.active = name
            self.active_changed.emit()

    def shapes_in(self, project: Project) -> list[Shape]:
        return self.local(project).shapes


def _references_valid(project: Project) -> bool:
    try:
        project.check_references()
    except ValueError:
        return False
    return True


def _holds_another_project(target: Path, own: Path | None) -> bool:
    """Whether saving in ``target`` would replace a project other than ``own``."""
    if own is not None and target.resolve() == own.resolve():
        return False
    if is_document(target):
        return target.exists()
    return is_project_folder(target)
