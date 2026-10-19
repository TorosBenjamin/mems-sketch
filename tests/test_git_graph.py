"""The commit graph: commits with their parents and labels from git, and the lanes
they are drawn in."""

from datetime import UTC, datetime

import pytest

from mems_sketch.storage import git

pytestmark = pytest.mark.skipif(not git.available(), reason="needs git")

DATE = datetime(2026, 10, 1, tzinfo=UTC)


def chain(*specs):
    """Commits from "sha:parent,parent" specs, newest first."""
    found = []
    for spec in specs:
        sha, _, parents = spec.partition(":")
        found.append(git.Commit(sha, "A", DATE, sha, tuple(p for p in parents.split(",") if p)))
    return found


def lanes(commits):
    pytest.importorskip("PySide6")
    from mems_sketch.gui.commit_graph import layout

    return layout(commits)


def test_a_straight_history_is_one_lane():
    rows = lanes(chain("c:b", "b:a", "a"))
    assert [r.lane for r in rows] == [0, 0, 0]
    assert [r.width for r in rows] == [1, 1, 1]
    assert rows[0].top == [] and rows[-1].bottom == []  # nothing above the newest, below the first


def test_a_branch_gets_a_lane_and_joins_where_it_started():
    #  d   (on main)
    #  | c (on a branch from b)
    #  |/
    #  b
    #  a
    rows = lanes(chain("d:b", "c:b", "b:a", "a"))
    assert [r.lane for r in rows] == [0, 1, 0, 0]
    assert rows[1].colour != rows[0].colour
    assert (1, 0, rows[1].colour) in rows[2].top  # the branch's line ends in b
    assert rows[2].width == 2 and rows[3].width == 1


def test_a_merge_draws_a_line_to_its_second_parent():
    #  m     (merges c into b's line)
    #  |\
    #  | c
    #  b |
    #  |/
    #  a
    rows = lanes(chain("m:b,c", "c:a", "b:a", "a"))
    merge = rows[0]
    assert merge.lane == 0
    assert (0, 1, rows[1].colour) in merge.bottom
    assert rows[1].lane == 1 and rows[2].lane == 0
    assert (1, 1, rows[1].colour) in rows[2].bottom  # c's line passes b
    assert [a for a, b, _ in rows[3].top if b == 0] == [0, 1]  # both end in a


def test_a_freed_lane_is_used_again():
    rows = lanes(chain("e:d", "x:d", "d:c", "y:c", "c"))
    assert [r.lane for r in rows] == [0, 1, 0, 1, 0]


# -- from git ------------------------------------------------------------------------------


def run(folder, *args):
    import subprocess

    subprocess.run(["git", *args], cwd=folder, check=True, capture_output=True)


@pytest.fixture
def repo(git_repo):
    """Two branches: main with a, b (tag v1), d; side from b with c."""
    folder = git_repo.parent
    run(folder, "checkout", "-q", "-b", "main")
    for name in ("a", "b"):
        (folder / f"{name}.txt").write_text(name)
        run(folder, "add", ".")
        run(folder, "commit", "-qm", f"Add {name}", "-m", f"Why {name}.")
    run(folder, "tag", "v1")
    run(folder, "checkout", "-q", "-b", "side")
    (folder / "c.txt").write_text("c")
    run(folder, "add", ".")
    run(folder, "commit", "-qm", "Add c")
    run(folder, "checkout", "-q", "main")
    (git_repo / "d.txt").write_text("d")  # in the project's folder
    run(folder, "add", ".")
    run(folder, "commit", "-qm", "Add d")
    return folder


def test_commits_come_with_parents_labels_and_their_message(repo):
    found = git.commits(repo)
    assert [c.subject for c in found] == ["Add d", "Add b", "Add a"]
    assert found[0].parents == (found[1].sha,)
    assert found[0].refs == (git.Ref("main", git.HEAD),)
    assert found[1].refs == (git.Ref("v1", git.TAG),)
    assert found[1].body == "Why b."
    assert found[0].body == ""


def test_commits_on_another_branch_or_all_of_them(repo):
    assert [c.subject for c in git.commits(repo, rev="side")] == ["Add c", "Add b", "Add a"]
    every = git.commits(repo, rev=git.ALL)
    assert sorted(c.subject for c in every) == ["Add a", "Add b", "Add c", "Add d"]
    assert every[-1].subject == "Add a"  # a parent after its children
    side = next(c for c in every if c.subject == "Add c")
    assert side.refs == (git.Ref("side", git.BRANCH),)


def test_commits_in_a_subfolder_skip_to_the_ones_that_changed_it(repo, git_repo):
    found = git.commits(git_repo)  # the project's folder: only "Add d" touched it
    assert [c.subject for c in found] == ["Add d"]
    assert found[0].parents == ()  # no earlier commit changed the folder


def test_branches_local_first_the_checked_out_one_marked(repo):
    run(repo, "update-ref", "refs/remotes/origin/main", "main")
    assert git.branches(repo) == [
        git.Ref("main", git.HEAD),
        git.Ref("side", git.BRANCH),
        git.Ref("origin/main", git.REMOTE),
    ]
    assert git.Ref("origin/main", git.REMOTE) in git.commits(repo)[0].refs


def test_a_detached_head_is_labelled_head(repo):
    run(repo, "checkout", "-q", "--detach", "side")
    assert git.Ref("HEAD", git.HEAD) in git.commits(repo)[0].refs
    assert git.branch(repo) is None
