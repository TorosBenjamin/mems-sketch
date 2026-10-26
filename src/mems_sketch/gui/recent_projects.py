"""The projects opened lately, for the Welcome window: kept in the settings.

Each is remembered by its folder, with its name, whether it is a library, when it
was last opened and whether it is pinned (pinned ones are listed first and stay
on the list). A project whose folder has gone is still listed, as missing, until
it is located again or removed.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from PySide6.QtCore import QSettings

from mems_sketch.gui.settings import APPLICATION, ORGANIZATION

KEY = "recent/projects"
MOST = 30  # projects kept (the pinned ones always are)


@dataclass(frozen=True)
class Recent:
    """A project on the list: its folder, name and kind, when it was last opened
    (seconds since the epoch) and whether it is pinned."""

    path: str
    name: str
    library: bool = False
    opened: float = 0.0
    pinned: bool = False

    @property
    def missing(self) -> bool:
        from mems_sketch.storage.project_files import is_project_folder

        return not is_project_folder(self.path)

    @property
    def branch(self) -> str | None:
        """The git branch the project's folder is on, if it is in a repository."""
        return git_branch(Path(self.path))


def _settings() -> QSettings:
    return QSettings(ORGANIZATION, APPLICATION)


def load() -> list[Recent]:
    """The list as shown: pinned first, then the most recently opened."""
    try:
        raw = json.loads(str(_settings().value(KEY, "[]") or "[]"))
    except ValueError:
        raw = []
    found = []
    for item in raw if isinstance(raw, list) else []:
        try:
            found.append(Recent(**{k: item[k] for k in item if k in Recent.__annotations__}))
        except (TypeError, KeyError):
            continue
    return sorted(found, key=lambda r: (not r.pinned, -r.opened))


def _store(items: list[Recent]) -> None:
    pinned = [r for r in items if r.pinned]
    others = sorted((r for r in items if not r.pinned), key=lambda r: -r.opened)
    kept = pinned + others[: max(0, MOST - len(pinned))]
    _settings().setValue(KEY, json.dumps([asdict(r) for r in kept]))


def folder_of(path: str | Path) -> str:
    """A project's folder, whether given the folder, its project file or a file in it."""
    from mems_sketch.storage.project_files import ProjectFormatError, project_folder

    try:
        return str(project_folder(path).resolve())
    except ProjectFormatError:  # a file not in a project: as it is
        return str(Path(path).resolve())


def remember(path: str | Path, name: str, library: bool = False) -> None:
    """``path`` was just opened (or created, or saved there): first on the list."""
    folder = folder_of(path)
    old = next((r for r in load() if r.path == folder), None)
    entry = Recent(folder, name, library, time.time(), old.pinned if old else False)
    _store([entry, *(r for r in load() if r.path != folder)])


def pin(path: str, pinned: bool = True) -> None:
    _store([replace(r, pinned=pinned) if r.path == path else r for r in load()])


def forget(path: str) -> None:
    """Take a project off the list (its files are left alone)."""
    _store([r for r in load() if r.path != path])


def locate(path: str, new: str | Path) -> None:
    """A missing project was found at ``new``: the list follows it there."""
    folder = folder_of(new)
    _store([replace(r, path=folder) if r.path == path else r for r in load() if r.path != folder])


def git_branch(folder: Path) -> str | None:
    """The branch checked out in the repository holding ``folder``, read from its
    ``.git/HEAD`` (no git needed); a detached head shows its commit."""
    for parent in (folder, *folder.parents):
        dot_git = parent / ".git"
        if dot_git.is_file():  # a worktree or submodule: "gitdir: <path>"
            text = dot_git.read_text(errors="replace").strip()
            if text.startswith("gitdir:"):
                dot_git = (parent / text.removeprefix("gitdir:").strip()).resolve()
        head = dot_git / "HEAD"
        if head.is_file():
            ref = head.read_text(errors="replace").strip()
            if ref.startswith("ref: refs/heads/"):
                return ref.removeprefix("ref: refs/heads/")
            return ref[:7] or None
    return None


def when(opened: float, now: float | None = None) -> str:
    """How long ago, in words: "just now", "5 minutes ago", "yesterday", "3 days ago"."""
    seconds = max(0.0, (now if now is not None else time.time()) - opened)
    minutes, hours, days = seconds / 60, seconds / 3600, seconds / 86400
    if minutes < 1:
        return "just now"
    if hours < 1:
        return f"{int(minutes)} minute{'s' if int(minutes) > 1 else ''} ago"
    if days < 1:
        return f"{int(hours)} hour{'s' if int(hours) > 1 else ''} ago"
    if days < 2:
        return "yesterday"
    if days < 31:
        return f"{int(days)} days ago"
    return time.strftime("%d %b %Y", time.localtime(opened))
