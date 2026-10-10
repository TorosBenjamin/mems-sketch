"""The engine's project model (C++, mems_sketch._core.Project) against Python's
(core-architecture.md, step 5): process constants, name resolution, reference
checks and parameter values agree, and fingerprints change on the same edits.

Skipped when the engine is not built, unless MGEOM_REQUIRED is set (CI)."""

import copy
import json
import os
from pathlib import Path

import pytest

from mems_sketch import ComponentDef, ParamDef, RectShape, RefShape, TransformShape
from mems_sketch.core.compiler import Compiler
from mems_sketch.core.imports import ImportedCell
from mems_sketch.core.project import Library, PrivateComponentError, Project, new_project
from mems_sketch.engine import project_data
from mems_sketch.storage import load

try:
    from mems_sketch import _core
except ImportError:
    try:
        import _core  # built in place: PYTHONPATH=build/engine
    except ImportError:
        if os.environ.get("MGEOM_REQUIRED"):
            raise
        pytest.skip("the engine (mems_sketch._core) is not built", allow_module_level=True)

EXAMPLES = Path(__file__).parent.parent / "examples"


def engine(project: Project):
    return _core.Project(json.dumps(project_data(project)))


def outcome(call):
    """What a call gives: its value, or the kind of error it raises."""
    try:
        return ("value", call())
    except PrivateComponentError:
        return ("private", None)
    except _core.PrivateComponentError:
        return ("private", None)
    except KeyError:
        return ("unknown", None)
    except ValueError as error:
        return ("invalid", str(error) if isinstance(error, _core.ModelError) else None)


def same(python, cpp) -> bool:
    if python[0] != cpp[0]:
        return False
    return python[0] != "value" or python[1] == cpp[1]


def ref(component, name=None, **params):
    return RefShape(name=name, component=component, params=params)


def component(name, parameters=(), shapes=()):
    return ComponentDef(name=name, parameters=list(parameters), shapes=list(shapes))


def nested_project() -> Project:
    """Private components at several depths, a library with a private component,
    an import, built-ins and process constants that use each other."""
    project = new_project("nested")
    project.process.constants = {"gap": 2.0, "pitch": "2 * gap + 1", "big": "pitch ** 2"}
    library = Library(
        name="std",
        components={
            "pad": component("pad", [ParamDef(name="size", default=40, min=1)], [ref("pad/via")]),
            "pad/via": component("pad/via", [ParamDef(name="d", default="2 * process.gap")]),
            "frame": component("frame", shapes=[ref("pad"), ref("anchor")]),
        },
    )
    project.libraries["std"] = library
    project.imports["logo"] = ImportedCell(
        name="logo", file="logo.gds", cell="LOGO", layers={"1/0": "metal"}, data=b"gds"
    )
    project.components.update(
        {
            "comb": component(
                "comb",
                [
                    ParamDef(name="fingers", default=4, integer=True, min=1, max=100),
                    ParamDef(name="width", default="process.gap * 2"),
                    ParamDef(name="pitch", default="width + process.pitch", internal=True),
                    ParamDef(name="length", default="fingers * pitch"),
                ],
                [ref("finger"), TransformShape(children=[ref("comb/finger")])],
            ),
            "comb/finger": component("comb/finger", [ParamDef(name="w", default=2)], [ref("tip")]),
            "comb/finger/tip": component("comb/finger/tip"),
            "spring": component(
                "spring", [ParamDef(name="k", default=0.5, min=0, max=1)], [ref("std.pad")]
            ),
        }
    )
    project.components["top"] = component(
        "top",
        [ParamDef(name="w", default=5)],
        [ref("comb"), ref("spring"), ref("std.frame"), ref("logo"), ref("comb_drive")],
    )
    return project


def projects() -> list[Project]:
    return [
        load(EXAMPLES / "resonator"),
        load(EXAMPLES / "libraries" / "mems_std"),
        nested_project(),
    ]


def broken_projects() -> list[Project]:
    """One problem each."""
    result = []

    def variant(change):
        project = nested_project()
        change(project)
        result.append(project)

    variant(lambda p: p.components["top"].shapes.append(ref("comb/finger")))  # private
    variant(lambda p: p.components["top"].shapes.append(ref("nothing")))  # unknown
    variant(lambda p: p.components["comb/finger/tip"].shapes.append(ref("comb")))  # a cycle
    variant(lambda p: p.components.update({"ghost/part": component("ghost/part")}))  # no owner
    variant(lambda p: p.libraries["std"].components["frame"].shapes.append(ref("spring")))
    variant(lambda p: p.process.constants.update({"bad": "missing + 1"}))
    variant(lambda p: p.process.constants.update({"a": "b", "b": "a"}))
    return result


def test_the_broken_projects_are_broken():
    for project in broken_projects():
        core = engine(project)
        assert (
            outcome(core.check_references)[0] != "value"
            or outcome(lambda core=core: core.scope)[0] != "value"
        )


@pytest.mark.parametrize("project", [*projects(), *broken_projects()], ids=lambda p: p.name)
def test_constants_and_references(project):
    core = engine(project)
    assert same(outcome(project.process.scope), outcome(lambda: core.scope))
    python, cpp = outcome(project.check_references), outcome(core.check_references)
    assert python[0] == cpp[0], (python, cpp)
    if python[0] == "invalid":  # one problem each: the same message
        with pytest.raises(ValueError) as error:
            project.check_references()
        assert cpp[1] == str(error.value)


def names_and_contexts(project: Project):
    names = {*project.components, *(n.rpartition("/")[2] for n in project.components)}
    for library, members in project.libraries.items():
        names |= {f"{library}.{n}" for n in members.components}
    names |= {*project.imports, "comb_drive", "anchor", "nothing", "std.nothing", "nope.pad"}
    contexts = [None, "", *project.components]
    contexts += [
        f"{library}.{n}" for library, lib in project.libraries.items() for n in lib.components
    ]
    return sorted(names), contexts


@pytest.mark.parametrize("project", projects(), ids=lambda p: p.name)
def test_names_resolve_alike(project):
    core = engine(project)
    names, contexts = names_and_contexts(project)
    for name in names:
        for context in contexts:
            python = outcome(lambda n=name, c=context: project.qualify(n, c))
            cpp = outcome(lambda n=name, c=context: core.qualify(n, c))
            assert same(python, cpp), (name, context, python, cpp)


def parameter_cases(definition: ComponentDef, constants):
    yield {}
    for p in definition.parameters:
        yield {p.name: 7}
        yield {p.name: 0}
        yield {p.name: -3}
        yield {p.name: 2.5}
        yield {p.name: "2 * 3"}
        for constant in constants:
            yield {p.name: f"process.{constant} + 1"}
        yield {p.name: "w_unknown"}
    if definition.parameters:
        yield {p.name: 3 for p in definition.parameters}
    yield {"no_such_parameter": 1}


@pytest.mark.parametrize("project", projects(), ids=lambda p: p.name)
def test_parameter_values_agree(project):
    core = engine(project)
    session = Compiler().session(project)
    pools = [("", project.components)]
    pools += [(f"{name}.", lib.components) for name, lib in project.libraries.items()]
    checked = 0
    for prefix, pool in pools:
        for name, definition in pool.items():
            for params in parameter_cases(definition, project.process.constants):
                python = outcome(lambda n=prefix + name, p=params: session.variables(n, p))
                cpp = outcome(lambda n=prefix + name, p=params: core.variables(n, p))
                assert same(python, cpp), (prefix + name, params, python, cpp)
                checked += 1
    assert checked > 20


def edits(project: Project):
    """(description, change) pairs, each applied to a copy of the project."""
    for name in project.components:
        yield (
            f"default of {name}",
            lambda p, n=name: p.components[n].parameters.append(
                ParamDef(name="added_parameter", default=1)
            ),
        )
        yield (
            f"shape in {name}",
            lambda p, n=name: p.components[n].shapes.append(
                RectShape(layer="device", x0=0, y0=0, x1=1, y1=1)
            ),
        )
    for library, lib in project.libraries.items():
        for name in lib.components:
            yield (
                f"shape in {library}.{name}",
                lambda p, b=library, n=name: (
                    p.libraries[b]
                    .components[n]
                    .shapes.append(RectShape(layer="device", x0=0, y0=0, x1=1, y1=1))
                ),
            )
    yield "a process constant", lambda p: p.process.constants.update({"extra": 1.0})
    if project.imports:
        yield "the imported file", lambda p: setattr(p.imports["logo"], "data", b"other")


def fingerprints(project: Project, names):
    python = Compiler().session(project)
    core = engine(project)
    return {n: python.fingerprint(n) for n in names}, {n: core.fingerprint(n) for n in names}


@pytest.mark.parametrize("project", projects(), ids=lambda p: p.name)
def test_fingerprints_change_on_the_same_edits(project):
    names = [*project.components]
    names += [
        f"{library}.{n}" for library, lib in project.libraries.items() for n in lib.components
    ]
    names += [*project.imports]
    python_before, core_before = fingerprints(project, names)
    assert len(set(core_before.values())) == len(set(python_before.values()))
    for description, change in edits(project):
        edited = copy.deepcopy(project)
        change(edited)
        python_after, core_after = fingerprints(edited, names)
        for name in names:
            python_changed = python_after[name] != python_before[name]
            core_changed = core_after[name] != core_before[name]
            assert python_changed == core_changed, (description, name)


def test_built_in_components_stay_with_python():
    core = engine(nested_project())
    assert core.qualify("comb_drive", "") == "comb_drive"
    with pytest.raises(_core.ModelError, match="built-in"):
        core.variables("comb_drive")
