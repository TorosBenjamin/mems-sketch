"""The project's history in git: what changed in a commit, or since the last
one (see :mod:`mems_sketch.core.diff` and :mod:`mems_sketch.storage.git`);
committing the project, and restoring an earlier version.

A version is a commit (any name git knows: ``HEAD``, a sha) or ``None``, the
project as it is being edited now, saved or not. Earlier versions are read
once and kept.

Restoring is an ordinary edit (one undo step): the design goes back to how it
was, and nothing in git changes; commit it to keep it. Committing saves the
project first and commits its folder only.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

from mems_sketch.core.compiler import Compiler
from mems_sketch.core.component import Geometry
from mems_sketch.core.diff import Change, diff_projects, geometry_changes
from mems_sketch.core.project import Project
from mems_sketch.storage import git

COMMIT_LIMIT = 200
MESSAGE_CHARS = 72  # a suggested commit message is at most this long


@dataclass
class Comparison:
    """Two versions of the project, and what changed from one to the other."""

    old: str | None  # the sha, or None for "before the first commit"
    new: str | None  # the sha, or None for the project being edited
    changes: list[Change] = field(default_factory=list)


class History:
    """See the module docstring. ``session.history``."""

    def __init__(self, session) -> None:
        self.session = session
        self.compiler = Compiler()  # earlier versions build here, not in the editor's cache
        self._versions: dict[str, Project | None] = {}

    @property
    def available(self) -> bool:
        """Whether the project is saved in a git repository."""
        return self.session.path is not None and git.repository(self.session.path) is not None

    def commits(self, limit: int = COMMIT_LIMIT) -> list[git.Commit]:
        """The commits that changed the project, newest first."""
        return git.commits(self.session.path, limit) if self.available else []

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

    def geometry(self, comparison: Comparison, component: str) -> tuple[Geometry, Geometry]:
        """The material added to and removed from ``component`` between the two
        versions (default parameters). Raises ValueError if a version does not build."""
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
                sides.append(project.render(component, compiler=self._compiler(rev)))
            except Exception as exc:
                raise ValueError(
                    f"the {label} version of '{component}' does not build: {exc}"
                ) from exc
        return geometry_changes(*sides)

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
        self.compiler.clear()

    def _compiler(self, rev: str | None) -> Compiler:
        return self.session.compiler if rev is None else self.compiler


def _has(project: Project, component: str) -> bool:
    try:
        project.qualify(component)
    except (KeyError, ValueError):
        return False
    return True
