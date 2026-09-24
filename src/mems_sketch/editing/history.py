"""The project's history in git, read-only: what changed in a commit, or since
the last one (see :mod:`mems_sketch.core.diff` and :mod:`mems_sketch.storage.git`).

A version is a commit (any name git knows: ``HEAD``, a sha) or ``None``, the
project as it is being edited now, saved or not. Earlier versions are read
once and kept.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mems_sketch.core.compiler import Compiler
from mems_sketch.core.component import Geometry
from mems_sketch.core.diff import Change, diff_projects, geometry_changes
from mems_sketch.core.project import Project
from mems_sketch.storage import git

COMMIT_LIMIT = 200


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
