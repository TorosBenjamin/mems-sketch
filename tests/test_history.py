"""The project's history in git: what changed, in words and in geometry."""

import pytest

from mems_sketch import BooleanShape, ComponentDef, Layer, ParamDef, Project, RectShape
from mems_sketch.core.diff import ADDED, CHANGED, REMOVED, diff_projects
from mems_sketch.editing import EditSession
from mems_sketch.storage import git

pytestmark = pytest.mark.skipif(not git.available(), reason="needs git")


def beam(**kw):
    return RectShape(name="beam", layer="device", x0=0, y0=0, x1="length", y1=10, **kw)


@pytest.fixture
def session(git_repo):
    project = Project(name="demo")
    project.add_layer(Layer("device", 1))
    project.components["top"].parameters.append(ParamDef(name="length", default=100))
    project.add(beam())
    session = EditSession(project)
    session.save(git_repo)
    return session


def texts(changes):
    return [(c.group, c.text) for c in changes]


def test_a_project_outside_git_has_no_history(tmp_path):
    session = EditSession()
    assert not session.history.available
    session.save(tmp_path / "plain")
    assert not session.history.available
    assert session.history.commits() == []


def test_before_the_first_commit_everything_is_new(session):
    assert session.history.available
    assert session.history.commits() == []
    changes = session.history.uncommitted().changes
    assert ("top", "component added") in texts(changes)


def test_commits_and_what_each_changed(session, commit):
    commit(session, "First sketch")
    session.parameters.update("length", default=120)
    commit(session, "Longer beam")
    first, second = reversed(session.history.commits())
    assert (first.subject, second.subject) == ("First sketch", "Longer beam")
    assert texts(session.history.commit(second.sha).changes) == [
        ("top", "parameter length: default 100 → 120")
    ]
    assert ("top", "component added") in texts(session.history.commit(first.sha).changes)
    assert session.history.uncommitted().changes == []


def test_uncommitted_changes_include_unsaved_ones(session, commit):
    commit(session, "First sketch")
    session.nodes.add(RectShape(name="pad", layer="device", x0=0, y0=20, x1=10, y1=30))
    assert session.dirty
    changes = session.history.uncommitted().changes
    assert texts(changes) == [("top", "shape pad added")]
    assert changes[0].path == ((0, 1),)


def test_the_geometry_added_and_removed(session, commit):
    commit(session, "First sketch")
    session.parameters.update("length", default=80)
    comparison = session.history.uncommitted()
    added, removed = session.history.geometry(comparison, "top")
    assert added.layers == {}
    assert removed.layers["device"].area() == 20 * 10 * 1e6  # µm² in dbu²


def test_an_earlier_version_is_read_from_git_not_the_disk(session, commit):
    commit(session, "First sketch")
    session.components.new("frame")
    commit(session, "Anchor")
    first = session.history.commits()[-1]
    earlier = session.history.version(first.sha)
    assert "frame" not in earlier.components
    assert "frame" in session.project.components


# -- the words ------------------------------------------------------------------


def project(*shapes, **components):
    p = Project()
    p.add_layer(Layer("device", 1))
    for shape in shapes:
        p.add(shape)
    p.components.update(components)
    return p


def rect(name=None, x0=0):
    return RectShape(name=name, layer="device", x0=x0, y0=0, x1=x0 + 10, y1=10)


def test_an_inserted_unnamed_shape_is_one_addition():
    old = project(rect(), rect(x0=20), rect(x0=40))
    new = project(rect(), rect(x0=5), rect(x0=20), rect(x0=40))
    assert texts(diff_projects(old, new)) == [("top", "shape rect added")]


def test_a_changed_shape_says_what_changed():
    old = project(rect("pad"), rect(x0=20))
    new = project(rect("pad", x0=5).model_copy(update={"enabled": False}), rect(x0=20))
    [change] = diff_projects(old, new)
    assert (change.action, change.subject) == (CHANGED, "pad")
    assert change.details == ("switched off", "x0 0 → 5", "x1 10 → 15")


def test_changes_inside_an_operation_name_the_way_there():
    def cut(x):
        return BooleanShape(name="cut", op="subtract", a=[rect("plate")], b=[rect("hole", x0=x)])

    changes = diff_projects(project(cut(2)), project(cut(4)))
    assert texts(changes) == [("top", "shape cut › hole: x0 2 → 4; x1 12 → 14")]
    assert changes[0].path == ((0, 0), (1, 0))


def test_removed_shapes_point_at_what_held_them():
    old = project(BooleanShape(name="cut", op="subtract", a=[rect("plate")], b=[rect("hole")]))
    new = project(BooleanShape(name="cut", op="subtract", a=[rect("plate")], b=[]))
    [change] = diff_projects(old, new)
    assert (change.action, change.subject, change.path) == (REMOVED, "cut › hole", ((0, 0),))


def test_process_imports_and_components():
    old = project(comb=ComponentDef(name="comb"))
    new = project(anchor=ComponentDef(name="anchor"))
    new.process.layers["device"] = Layer("device", 2)
    new.add_layer(Layer("metal", 5))
    assert texts(diff_projects(old, new)) == [
        ("Process", "layer device: gds 1/0 → 2/0"),
        ("Process", "layer metal added: GDS 5/0"),
        ("anchor", "component added"),
        ("comb", "component removed"),
    ]
    assert {c.action for c in diff_projects(old, new)} == {ADDED, REMOVED, CHANGED}
