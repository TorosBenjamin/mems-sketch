"""The project's history in git: what changed in a commit, or since the last
one (see :mod:`mems_sketch.core.diff` and :mod:`mems_sketch.storage.git`);
committing the project, and restoring an earlier version.

A version is a commit (any name git knows: ``HEAD``, a sha) or ``None``, the
project as it is being edited now, saved or not. Earlier versions are read
once and kept.

Restoring is an ordinary edit (one undo step): the design goes back to how it
was, and nothing in git changes; commit it to keep it. Committing saves the
project first and commits its folder only.

The local changes (:meth:`History.local`) group the uncommitted changes by
what holds them: a component (with its private ones), or the project, its
process or its imports; and list the other files in the folder git sees
changed or does not track. Committing some of them commits the files that
hold them. The project file lists the components, so it goes with any
component added, removed or moved to another category.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

from mems_sketch.core.component import Geometry
from mems_sketch.core.diff import (
    ADDED,
    CHANGED,
    IMPORTS,
    PROCESS,
    PROJECT,
    REMOVED,
    Change,
    diff_projects,
    geometry_changes,
)
from mems_sketch.core.project import Project
from mems_sketch.core.shapes import container_of, paths
from mems_sketch.engine import Build, Engine
from mems_sketch.storage import git
from mems_sketch.storage.project_files import (
    IMPORTS_DIR,
    PROCESSES_DIR,
    PROJECT_FILE,
    ProjectFormatError,
    component_folder,
    manifest_file,
    written_by_project,
)

COMMIT_LIMIT = 200
MESSAGE_CHARS = 72  # a suggested commit message is at most this long


@dataclass
class Comparison:
    """Two versions of the project, and what changed from one to the other."""

    old: str | None  # the sha, or None for "before the first commit"
    new: str | None  # the sha, or None for the project being edited
    changes: list[Change] = field(default_factory=list)


@dataclass
class LocalGroup:
    """Uncommitted changes held together: a component and its private ones (``key``
    is its name), or ``PROJECT``, ``PROCESS`` or ``IMPORTS``."""

    key: str
    action: str  # ADDED, REMOVED or CHANGED: what happened to it as a whole
    changes: list[Change] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)  # the files holding it, in the folder

    @property
    def component(self) -> bool:
        return self.key not in (PROJECT, PROCESS, IMPORTS)


@dataclass
class LocalChanges:
    """What committing could take: the project's changes by group, and the other
    files in the folder (changed tracked ones, and untracked ones)."""

    groups: list[LocalGroup] = field(default_factory=list)
    files: list[git.FileStatus] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.groups or self.files)


class History:
    """See the module docstring. ``session.history``."""

    def __init__(self, session) -> None:
        self.session = session
        self.engine = Engine()  # earlier versions build here, not in the editor's cache
        self._versions: dict[str, Project | None] = {}

    @property
    def available(self) -> bool:
        """Whether the project is saved in a git repository."""
        return self.session.path is not None and git.repository(self.session.path) is not None

    def commits(
        self,
        limit: int = COMMIT_LIMIT,
        rev: str | None = None,
        component: str | None = None,
    ) -> list[git.Commit]:
        """The commits that changed the project (or ``component`` and its private
        ones), newest first: on the branch checked out, on ``rev`` (a branch), or on
        every branch (``git.ALL``)."""
        if not self.available:
            return []
        path = None
        if component is not None:
            path = component_folder(self.session.project.components, component)
        return git.commits(self.session.path, limit, rev, path)

    def branches(self) -> list[git.Ref]:
        """The local branches (the one checked out has ``role`` HEAD), then the remote ones."""
        return git.branches(self.session.path) if self.available else []

    def version(self, rev: str | None) -> Project | None:
        """The project at ``rev`` (None: as it is now); None if it did not exist then."""
        if rev is None:
            return self.session.project
        sha = git.resolve(self.session.path, rev)
        if sha is None:
            raise ValueError(f"no commit '{rev}'")
        if sha not in self._versions:
            self._versions[sha] = git.load_at(self.session.path, sha)
        return self._versions[sha]

    def uncommitted(self) -> Comparison:
        """The last commit against the project being edited (unsaved changes too)."""
        head = git.resolve(self.session.path, "HEAD") if self.available else None
        return self.compare(head, None)

    def commit(self, rev: str) -> Comparison:
        """What ``rev`` changed: the commit before it against it."""
        sha = git.resolve(self.session.path, rev)
        if sha is None:
            raise ValueError(f"no commit '{rev}'")
        return self.compare(git.parent(self.session.path, sha), sha)

    def compare(self, old: str | None, new: str | None) -> Comparison:
        """``old`` (None: nothing) against ``new`` (None: the project now)."""
        before = self.version(old) if old is not None else None
        after = self.version(new)
        if after is None:
            raise ValueError("the project is not in that commit")
        return Comparison(old, new, diff_projects(before, after))

    def builds(self, comparison: Comparison, component: str) -> tuple[Build | None, Build | None]:
        """``component`` built in the two versions (default parameters), None where it
        does not exist. Raises ValueError if a version does not build."""
        sides = []
        for rev, label, exists in (
            (comparison.old, "earlier", comparison.old is not None),
            (comparison.new, "later", True),
        ):
            project = self.version(rev) if exists else None
            if project is None or not _has(project, component):
                sides.append(None)
                continue
            try:
                build = self._engine(rev).trial(project).build(component)
                build.geometry  # noqa: B018 - built now, so that a failure is reported here
            except Exception as exc:
                raise ValueError(
                    f"the {label} version of '{component}' does not build: {exc}"
                ) from exc
            sides.append(build)
        return sides[0], sides[1]

    def geometry(self, comparison: Comparison, component: str) -> tuple[Geometry, Geometry]:
        """The material added to and removed from ``component`` between the two
        versions (default parameters). Raises ValueError if a version does not build."""
        old, new = self.builds(comparison, component)
        return geometry_changes(
            old.geometry if old is not None else None, new.geometry if new is not None else None
        )

    def rollback_shape(self, comparison: Comparison, change: Change) -> None:
        """Bring one shape back as in the last commit (one undoable edit): a changed
        one as it was, an added one removed. Only for the uncommitted changes."""
        if comparison.new is not None or change.what != "shape" or change.path is None:
            raise ValueError("only an uncommitted change to a shape can be rolled back")
        component, name = change.component, change.subject.rsplit(" › ", 1)[-1]
        old = self.version(comparison.old) if comparison.old is not None else None
        before = None
        if change.action != ADDED:
            definition = old.components.get(component) if old is not None else None
            found = [s for _, s in paths(definition.shapes) if s.name == name] if definition else []
            if not found or change.action == REMOVED:
                raise ValueError(f"'{name}' cannot be brought back from here")
            before = found[0].model_copy(deep=True)

        def change_(project: Project) -> None:
            container, index = container_of(project.components[component].shapes, change.path)
            if before is None:
                del container[index]
            else:
                container[index] = before

        verb = "Remove" if before is None else "Roll back"
        self.session.edit(f"{verb} {name}", change_)

    # -- writing -------------------------------------------------------------------

    @property
    def branch(self) -> str | None:
        return git.branch(self.session.path) if self.available else None

    def can_init(self) -> bool:
        """Whether the project is saved outside any repository (so one can be made)."""
        path = self.session.path
        return path is not None and git.available() and git.repository(path) is None

    def init(self) -> Path:
        """Make the project's folder a git repository (nothing is committed yet)."""
        if not self.can_init():
            raise ValueError("save the project in a folder outside a git repository first")
        return git.init(self.session.path)

    def commit_changes(self, message: str) -> str:
        """Save the project and commit its folder (only it); returns the new sha."""
        if not self.available:
            raise ValueError("the project is not in a git repository")
        self.session.save()
        try:
            sha = git.commit(self.session.path, message)
        except git.GitError as exc:
            raise ValueError(str(exc)) from exc
        self.session.changed.emit()  # the uncommitted changes are gone
        return sha

    def local(
        self,
        comparison: Comparison | None = None,
        files: list[git.FileStatus] | None = None,
    ) -> LocalChanges:
        """The uncommitted changes, grouped (see the module docstring). Pass the
        :meth:`uncommitted` comparison or :meth:`other_files` when already known."""
        if not self.available:
            return LocalChanges()
        comparison = comparison or self.uncommitted()
        old = self.version(comparison.old) if comparison.old is not None else None
        new = self.session.project
        groups = grouped(comparison.changes)
        try:
            manifest = manifest_file(self.session.path).name
        except ProjectFormatError:  # not saved yet
            manifest = PROJECT_FILE
        for group in groups:
            group.paths = _paths(group.key, old, new, manifest)
        files = self.other_files() if files is None else files
        return LocalChanges(groups, files)

    def other_files(self) -> list[git.FileStatus]:
        """The files in the folder, besides the project's own, that differ from the
        last commit or are untracked (read from the disk)."""
        if not self.available:
            return []
        try:
            return [f for f in git.status(self.session.path) if not written_by_project(f.path)]
        except git.GitError:
            return []

    def commit_local(
        self, message: str, keys: list[str], files: list[str], amend: bool = False
    ) -> str:
        """Save the project and commit the groups ``keys`` of :meth:`local` and the
        other ``files``; ``amend`` replaces the last commit. Returns the new sha."""
        if not self.available:
            raise ValueError("the project is not in a git repository")
        local = self.local()
        paths = [p for g in local.groups if g.key in keys for p in g.paths]
        self.session.save()
        try:
            sha = git.commit(self.session.path, message, [*paths, *files], amend)
        except git.GitError as exc:
            raise ValueError(str(exc)) from exc
        self.session.changed.emit()
        return sha

    def last_message(self) -> str:
        return git.last_message(self.session.path) if self.available else ""

    def ahead_behind(self) -> tuple[int, int] | None:
        return git.ahead_behind(self.session.path) if self.available else None

    def track(self, files: list[str]) -> None:
        """Have git track untracked ``files`` (they then go into commits)."""
        self._git(git.add, files)

    def ignore(self, file: str) -> None:
        self._git(git.ignore, file)

    def discard(self, files: list[str]) -> None:
        """Put changed tracked ``files`` back as in the last commit (no undo)."""
        self._git(git.discard, files)

    def rollback(self, key: str) -> None:
        """Bring a group of :meth:`local` back as in the last commit: one undoable
        edit. A component added since is removed."""
        sha = git.resolve(self.session.path, "HEAD") if self.available else None
        if sha is None:
            raise ValueError("there is no commit to go back to yet")
        old = self.version(sha) or Project(top=None)
        if key not in _GROUPS:
            if key in old.components:
                self.restore(sha, key)
                return

            def remove(project: Project) -> None:
                project.components = {
                    n: d
                    for n, d in project.components.items()
                    if n != key and not n.startswith(key + "/")
                }

            self.session.edit(f"Roll back {key}", remove)
            return

        def change(project: Project) -> None:
            if key == PROJECT:
                project.name, project.top = old.name, old.top
            elif key == PROCESS:
                project.processes = copy.deepcopy(old.processes)
                project.process_name, project.reasons = old.process_name, dict(old.reasons)
                own = project.processes.get(old.process_name)
                project.process = own if own is not None else copy.deepcopy(old.process)
            else:
                project.imports = copy.deepcopy(old.imports)

        self.session.edit(f"Roll back the {key.lower()}", change)

    def _git(self, action, *args) -> None:
        if not self.available:
            raise ValueError("the project is not in a git repository")
        try:
            action(self.session.path, *args)
        except git.GitError as exc:
            raise ValueError(str(exc)) from exc
        self.session.changed.emit()

    def staged_elsewhere(self) -> list[str]:
        """Files staged outside the project folder: its commits leave them alone."""
        return git.staged_elsewhere(self.session.path) if self.available else []

    def suggested_message(self, comparison: Comparison | None = None) -> str:
        """A commit message from what changed: ``Change slot_x; add anchor_2``."""
        comparison = comparison or self.uncommitted()
        if comparison.old is None and comparison.changes:
            return f"Start {self.session.project.name}"
        verbs = {"added": "add", "removed": "remove", "changed": "change"}
        parts: list[str] = []
        for change in comparison.changes:
            if change.what == "component":
                name = change.group
            elif change.what == "shape":
                name = change.subject.rsplit(" › ", 1)[-1]  # the shape itself
            elif change.what in ("project", "description"):
                name = (
                    f"{change.group} {change.what}" if change.what == "description" else "project"
                )
            else:
                name = f"{change.what} {change.subject}"  # "layer metal"
            part = f"{verbs[change.action]} {name}"
            if part not in parts:
                parts.append(part)
        if not parts:
            return ""
        text = "; ".join(parts)
        if len(text) > MESSAGE_CHARS:
            kept = []
            for part in parts:
                if len("; ".join([*kept, part])) > MESSAGE_CHARS - 12:
                    break
                kept.append(part)
            text = "; ".join(kept) + f" and {len(parts) - len(kept)} more"
        return text[0].upper() + text[1:]

    def restore(self, rev: str, component: str | None = None) -> None:
        """Bring back the project (or one local component and its private ones) as
        it was at ``rev``: one undoable edit; git is not touched."""
        sha = git.resolve(self.session.path, rev) if self.available else None
        if sha is None:
            raise ValueError(f"no commit '{rev}'")
        old = self.version(sha)
        if old is None:
            raise ValueError("the project is not in that commit")
        short = sha[:7]
        if component is None:

            def change(project: Project) -> None:
                project.name, project.top = old.name, old.top
                project.process = copy.deepcopy(old.process)
                project.components = {n: d.model_copy(deep=True) for n, d in old.components.items()}
                project.imports = copy.deepcopy(old.imports)

            self.session.edit(f"Restore the project from {short}", change)
            return
        if component not in old.components:
            raise ValueError(f"'{component}' is not in {short}")

        def mine(name: str) -> bool:
            return name == component or name.startswith(component + "/")

        restored = {n: d for n, d in old.components.items() if mine(n)}

        def change(project: Project) -> None:
            result = {}  # in the current order; ones that came back at the end
            for name, definition in project.components.items():
                if not mine(name):
                    result[name] = definition
                elif name in restored:
                    result[name] = restored[name].model_copy(deep=True)
            for name, definition in restored.items():
                result.setdefault(name, definition.model_copy(deep=True))
            project.components = result

        self.session.edit(f"Restore {component} from {short}", change)

    def forget(self) -> None:
        """Drop the versions read so far (another project was opened)."""
        self._versions.clear()
        self.engine.clear()

    def _engine(self, rev: str | None) -> Engine:
        return self.session.engine if rev is None else self.engine


_GROUPS = (PROJECT, PROCESS, IMPORTS)


def grouped(changes: list[Change]) -> list[LocalGroup]:
    """Changes by what holds them: a component with its private ones, the project,
    its process, its imports (in the order they come)."""
    groups: dict[str, LocalGroup] = {}
    for change in changes:
        key = change.group if change.group in _GROUPS else change.group.split("/")[0]
        group = groups.get(key)
        if group is None:
            group = groups[key] = LocalGroup(key, CHANGED)
        if change.what == "component" and change.group == key:
            group.action = change.action
        group.changes.append(change)
    return list(groups.values())


def _paths(key: str, old: Project | None, new: Project, manifest: str = PROJECT_FILE) -> list[str]:
    """The files in the project folder that hold a group of local changes
    (``manifest``: the project file's name there)."""
    if key == PROJECT:
        return [manifest]
    if key == PROCESS:
        return [manifest, PROCESSES_DIR]  # overrides are in the project file
    if key == IMPORTS:
        return [manifest, IMPORTS_DIR]
    places = [
        component_folder(project.components, key)
        for project in (old, new)
        if project is not None and key in project.components
    ]
    if len(set(places)) != 1 or old is None or key not in old.components:
        places.append(manifest)  # added, removed or moved: the manifest lists it
    elif key not in new.components:
        places.append(manifest)
    return list(dict.fromkeys(places))


def _has(project: Project, component: str) -> bool:
    try:
        project.qualify(component)
    except (KeyError, ValueError):
        return False
    return True
