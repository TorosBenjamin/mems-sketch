"""A project folder's history in git: its commits, the project as it was, and
committing it.

Only the project folder is ever committed: files staged elsewhere in the
repository stay staged and out of the commit. Nothing here checks out,
resets, branches or talks to a remote. The ``git`` program does the work (no
library needed); a folder outside a repository, or a machine without git,
simply has no history.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mems_sketch.core.project import Project
from mems_sketch.storage.project_files import PROJECT_FILE, load_project

FIELD, RECORD = "\x1f", "\x1e"  # separators in git log's output
TIMEOUT_S = 30


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Commit:
    sha: str
    author: str
    date: datetime
    subject: str

    @property
    def short(self) -> str:
        return self.sha[:7]


def available() -> bool:
    return shutil.which("git") is not None


def repository(folder: str | Path) -> Path | None:
    """The top folder of the repository holding ``folder``, or None."""
    if not available() or not Path(folder).is_dir():
        return None
    try:
        return Path(_git(folder, "rev-parse", "--show-toplevel").strip())
    except GitError:
        return None


def commits(folder: str | Path, limit: int = 200) -> list[Commit]:
    """The commits that changed the project in ``folder``, newest first."""
    folder = Path(folder)
    if repository(folder) is None:
        return []
    try:
        out = _git(
            folder,
            "log",
            f"-n{limit}",
            f"--format=%H{FIELD}%an{FIELD}%aI{FIELD}%s{RECORD}",
            "--",
            ".",
        )
    except GitError:  # e.g. a repository without commits yet
        return []
    result = []
    for record in out.split(RECORD):
        record = record.strip()
        if record:
            sha, author, date, subject = record.split(FIELD, 3)
            result.append(Commit(sha, author, datetime.fromisoformat(date), subject))
    return result


def parent(folder: str | Path, rev: str) -> str | None:
    """The commit before ``rev`` (its first parent), or None for the first commit."""
    try:
        return _git(folder, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}^").strip()
    except GitError:
        return None


def resolve(folder: str | Path, rev: str) -> str | None:
    """The full sha of ``rev`` (``HEAD``, a branch, a short sha), or None."""
    try:
        return _git(folder, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}").strip()
    except GitError:
        return None


def load_at(folder: str | Path, rev: str) -> Project | None:
    """The project in ``folder`` as it was at ``rev``, or None if it did not exist
    then. Libraries are the current ones (they are not part of its history)."""
    folder = Path(folder).resolve()
    root = repository(folder)
    if root is None:
        raise GitError(f"{folder} is not in a git repository")
    relative = folder.relative_to(root.resolve()).as_posix()
    spec = "." if relative == "." else relative
    try:
        data = _git(root, "archive", "--format=tar", rev, "--", spec, binary=True)
    except GitError:  # the folder did not exist in that commit
        return None
    with tempfile.TemporaryDirectory(prefix="mems-sketch-") as tmp:
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            if hasattr(tarfile, "data_filter"):  # Python 3.11.4 and later
                archive.extractall(tmp, filter="data")
            else:
                archive.extractall(tmp)  # the user's own repository
        copy = Path(tmp) / relative
        if not (copy / PROJECT_FILE).is_file():
            return None
        return load_project(copy, libraries_from=folder)


def branch(folder: str | Path) -> str | None:
    """The current branch (also before its first commit), or None when detached."""
    try:
        return _git(folder, "symbolic-ref", "--quiet", "--short", "HEAD").strip() or None
    except GitError:
        return None


def identity(folder: str | Path) -> tuple[str, str] | None:
    """The name and email git commits with here, or None if either is not set."""
    try:
        name = _git(folder, "config", "user.name").strip()
        email = _git(folder, "config", "user.email").strip()
    except GitError:
        return None
    return (name, email) if name and email else None


def pending(folder: str | Path) -> list[str]:
    """Files in the project folder that differ from the last commit (new ones too)."""
    out = _git(folder, "status", "--porcelain", "--untracked-files=all", "--", ".")
    return [line[3:] for line in out.splitlines() if line.strip()]


def staged_elsewhere(folder: str | Path) -> list[str]:
    """Files staged outside the project folder (left out of its commits)."""
    folder = Path(folder).resolve()
    root = repository(folder)
    if root is None:
        return []
    relative = folder.relative_to(root.resolve()).as_posix()
    prefix = "" if relative == "." else relative + "/"
    staged = _git(root, "diff", "--cached", "--name-only").splitlines()
    return [f for f in staged if f and not f.startswith(prefix)]


def commit(folder: str | Path, message: str) -> str:
    """Commit everything in the project folder (and nothing else); returns the sha."""
    if not message.strip():
        raise GitError("a commit needs a message")
    if identity(folder) is None:
        raise GitError(
            "git does not know who you are yet. Set your name and email once, e.g. "
            'git config --global user.name "Your Name" and '
            "git config --global user.email you@example.com"
        )
    if not pending(folder):
        raise GitError("nothing to commit: the project is as in the last commit")
    _git(folder, "add", "--all", "--", ".")
    _git(folder, "commit", "--quiet", "--message", message, "--", ".")
    return _git(folder, "rev-parse", "HEAD").strip()


def init(folder: str | Path) -> Path:
    """Make ``folder`` a new git repository; returns it."""
    _git(folder, "init", "--quiet")
    return Path(folder)


def _git(folder: str | Path, *args: str, binary: bool = False):
    try:
        done = subprocess.run(
            ["git", "-C", str(folder), *args],
            capture_output=True,
            check=False,
            timeout=TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(str(exc)) from exc
    if done.returncode != 0:
        raise GitError(done.stderr.decode(errors="replace").strip() or f"git {args[0]} failed")
    return done.stdout if binary else done.stdout.decode("utf-8", errors="replace")
