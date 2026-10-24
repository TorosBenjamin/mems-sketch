"""Command-line interface to the engine: project files in, results out.

    mems-sketch-cli new     my_project
    mems-sketch-cli info    my_project
    mems-sketch-cli check   my_project [--component plate] [--set pitch=15] [--json] [--strict]
    mems-sketch-cli rules                                           (rule kinds, their parameters)
    mems-sketch-cli export  my_project out.gds [--set pitch=15] [--option grid_um=0.005]
    mems-sketch-cli formats                                         (export formats, their options)
    mems-sketch-cli convert my_project design.json                  (and back; .xml .mat .yaml)
    mems-sketch-cli mcp     [my_project]                            (an MCP server, on stdio)

``check`` exits with status 1 when a rule is violated with severity error (or
any rule, with ``--strict``), so it can gate CI. Errors exit with status 2. Nothing here depends on the GUI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mems_sketch.core.component import Geometry
from mems_sketch.core.errors import explain
from mems_sketch.core.process import changes_between
from mems_sketch.core.project import Project, new_project
from mems_sketch.engine import Build, Engine
from mems_sketch.export.base import (
    PROCESS_DEFAULTS,
    available_exporters,
    cell_name_notes,
    export_with_report,
    exporter_class,
    format_for,
    options_of,
    title_of,
)
from mems_sketch.process import rules
from mems_sketch.storage import is_document, load, save


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except BrokenPipeError:  # `info | head` stopped reading: nothing went wrong here
        sys.stdout = open(os.devnull, "w")  # noqa: SIM115 - Python flushes it on exit
        return 0
    except Exception as exc:  # noqa: BLE001 - reported as a CLI error
        print(f"error: {explain(exc)}", file=sys.stderr)
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mems-sketch-cli", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    new = commands.add_parser("new", help="create an empty project folder")
    new.add_argument("folder", type=Path)
    new.add_argument("--name", help="project name (default: folder name)")
    new.add_argument(
        "--library", action="store_true", help="a library: components only, no top component"
    )
    new.add_argument(
        "--process",
        help="a bundled library's process to use (mems.surface, mems.polysilicon, mems.soi); "
        "default: a process of its own with device, anchor and metal layers",
    )
    new.set_defaults(handler=_new)

    new_process = commands.add_parser(
        "new-process", help="create a process library: a process of your own to fill in"
    )
    new_process.add_argument("folder", type=Path)
    new_process.add_argument("--name", help="the process's name (default: the folder's name)")
    new_process.add_argument(
        "--from",
        dest="start",
        default="mems.surface",
        help="the process it starts as a copy of (default mems.surface; 'empty' for none)",
    )
    new_process.set_defaults(handler=_new_process)

    mcp = commands.add_parser(
        "mcp", help="serve the design tools to AI assistants (MCP, on stdio; the mcp extra)"
    )
    mcp.add_argument("project", type=Path, nargs="?", help="a project to open first")
    mcp.set_defaults(handler=_mcp)

    info = commands.add_parser("info", help="list components, parameters and layers")
    info.add_argument("project", type=Path, help="project folder, project.yaml or one file")
    info.add_argument("--component", help="only this component (also a library's: mems.anchor)")
    info.add_argument("--json", action="store_true", help="print it as JSON")
    info.set_defaults(handler=_info)

    check = commands.add_parser("check", help="run design-rule checks (exit 1 on violations)")
    _geometry_arguments(check)
    check.add_argument("--json", action="store_true", help="print violations as JSON")
    check.add_argument(
        "--strict", action="store_true", help="fail on warnings too, not only on errors"
    )
    check.set_defaults(handler=_check)

    exp = commands.add_parser("export", help="write geometry to a file (format from extension)")
    _geometry_arguments(exp)
    exp.add_argument("output", type=Path)
    exp.add_argument("--format", choices=sorted(available_exporters()), help="override format")
    exp.add_argument(
        "-O",
        "--option",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="a setting of the format (see 'formats'); may be repeated",
    )
    exp.set_defaults(handler=_export)

    fmt = commands.add_parser("formats", help="list the export formats and their options")
    fmt.set_defaults(handler=_formats)

    kinds = commands.add_parser("rules", help="list the rule kinds and their parameters")
    kinds.set_defaults(handler=_rule_kinds)

    convert = commands.add_parser(
        "convert",
        help="translate a project between a folder and one file (.json, .xml, .mat, .yaml); "
        "also reads legacy .mems files",
    )
    convert.add_argument("source", type=Path, help="project folder, project.yaml or one file")
    convert.add_argument("target", type=Path, help="a folder, or a file named by its format")
    convert.set_defaults(handler=_convert)
    return parser


def _geometry_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("project", type=Path, help="project folder or project.yaml")
    parser.add_argument("--component", help="component to compile (default: the top component)")
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="override a parameter (number or expression); may be repeated",
    )


def _parameters(assignments: list[str]) -> dict[str, float | str]:
    params: dict[str, float | str] = {}
    for assignment in assignments:
        name, sep, value = assignment.partition("=")
        if not sep or not name.strip():
            raise ValueError(f"--set expects NAME=VALUE, got '{assignment}'")
        try:
            params[name.strip()] = float(value)
        except ValueError:
            params[name.strip()] = value.strip()
    return params


def _build(project: Project, component: str | None, params: dict[str, Any]) -> Build:
    """``component``, or the top component when none is named."""
    return Engine().load(project).build(_component(project, component), params)


def _component(project: Project, component: str | None) -> str:
    """``component``, or the top component; a library's must be named."""
    name = component or project.top
    if name is None:
        names = ", ".join(sorted(project.components)) or "none yet"
        raise ValueError(
            f"'{project.name}' is a library, with no top component: name one with "
            f"--component (its components: {names})"
        )
    return name


def _values(project: Project, component: str | None, assignments: list[str]) -> dict[str, float]:
    """The ``--set`` values, checked and worked out: an expression may use the
    component's other parameters (``pitch=plate/16``), as a default can."""
    params = _parameters(assignments)
    if not params:
        return {}
    name = _component(project, component)
    resolved = Engine().load(project).variables(name, params)
    return {key: resolved[key] for key in params}


def _geometry(project: Project, args: argparse.Namespace) -> Geometry:
    return _build(project, args.component, _values(project, args.component, args.set)).geometry


def _new(args: argparse.Namespace) -> int:
    if (args.folder / "project.yaml").exists():
        raise FileExistsError(f"{args.folder} already contains a project")
    project = new_project(args.name or args.folder.name, library=args.library)
    if args.process:
        if args.process not in [n for n in project.process_names() if "." in n]:
            raise ValueError(f"no process '{args.process}' ({_process_list(project)})")
        project.use_process(args.process)
    save(project, args.folder)
    print(f"created {args.folder}")
    return 0


def _new_process(args: argparse.Namespace) -> int:
    """A library holding one process, a copy of ``--from``: what to edit into your
    fab's process (layers, levels, constants, rules), then add as a library."""
    import copy

    from mems_sketch.core.process import Process
    from mems_sketch.core.project import Project

    if (args.folder / "project.yaml").exists():
        raise FileExistsError(f"{args.folder} already contains a project")
    name = args.name or args.folder.name
    if not name.isidentifier():
        raise ValueError(f"'{name}' is not a valid process name: use letters, digits and _")
    if args.start == "empty":
        process = Process(description="")
    else:
        source = Project(name="source")
        library, _, process_name = args.start.partition(".")
        if (
            library not in source.libraries
            or process_name not in source.libraries[library].processes
        ):
            raise ValueError(
                f"no process '{args.start}' to start from ({_process_list(source)}, or empty)"
            )
        process = copy.deepcopy(source.libraries[library].processes[process_name])
        process.description = f"Started as a copy of {args.start}: make it your fab's process."
    project = Project(
        name=name, top=None, components={}, processes={name: process}, process_name=name
    )
    save(project, args.folder)
    print(f"created {args.folder}: edit processes/{name}/process.yaml, then add it as a library")
    return 0


def _process_list(project: Project) -> str:
    """The bundled libraries' processes, to choose from."""
    return "the processes: " + ", ".join(n for n in project.process_names() if "." in n)


def _mcp(args: argparse.Namespace) -> int:
    try:
        from mems_sketch.mcp_server.server import run
    except ImportError as error:
        raise ValueError(
            f"the MCP server needs the mcp extra: pip install 'mems-sketch[mcp]' ({error})"
        ) from error
    run(str(args.project) if args.project else None)
    return 0


def _info(args: argparse.Namespace) -> int:
    project = load(args.project)
    if args.component:
        return _component_info(project, args.component, args.json)
    if args.json:
        print(json.dumps(_project_data(project), indent=2))
        return 1 if project.validate() else 0
    kind = f"top: {project.top}" if project.top else "library, no top component"
    print(f"project {project.name} ({kind})")
    for note in project.load_notes:
        print(f"note: {note}")
    process = project.process
    print(
        f"process: {project.process_name} (grid {_number(process.grid_um)} µm, "
        f"curves within {_number(process.chord_um)} µm)"
    )
    if project.base_process is not None:
        constants, changed, added = changes_between(project.base_process, project.process)
        for name, value in constants.items():
            print(f"  changed process.{name} = {_number(value)}")
        for name in [*changed, *added]:
            reason = project.reasons.get(name)
            print(
                f"  {'changed' if name in changed else 'added'} rule {name}"
                + (f": {reason}" if reason else "")
            )
    print("layers:")
    for layer in project.layers.values():
        print(f"  {layer.name:<12} gds {layer.gds_layer}/{layer.gds_datatype}")
    if project.process.rules:
        print("rules:")
    for rule in project.process.rules.values():
        _print_rule(rule.name, rule)
    if project.process.constants:
        print("process constants:")
        for name, value in project.process.constants.items():
            print(f"  process.{name} = {_number(value)}")
    print("components:")
    for name, definition in project.components.items():
        params = ", ".join(f"{p.name}={_number(p.default)}" for p in definition.parameters)
        marker = "*" if name == project.top else " "
        print(f" {marker}{name}({params})")
    for library in project.libraries.values():
        print(f"library {library.name}: {', '.join(library.components)}")
    problems = project.validate()
    for problem in problems:
        print(f"problem: {problem}")
    return 1 if problems else 0


def _print_rule(name: str, rule, note: str = "") -> None:
    values = ", ".join(f"{k}={_number(v)}" for k, v in rule.values.items())
    state = "" if rule.enabled else "  (off)"
    print(
        f"  {name:<20} {rule.kind}({', '.join(rule.layers)}) {values}  {rule.severity}{state}{note}"
    )


def _project_data(project: Project) -> dict[str, Any]:
    process = project.process
    return {
        "name": project.name,
        "top": project.top,
        "notes": project.load_notes,
        "process": {
            "name": project.process_name,
            "grid_um": process.grid_um,
            "chord_um": process.chord_um,
            "constants": dict(process.constants),
            "layers": {
                n: [layer.gds_layer, layer.gds_datatype] for n, layer in project.layers.items()
            },
            "rules": {
                n: {"kind": r.kind, "layers": list(r.layers), "values": dict(r.values)}
                for n, r in process.rules.items()
            },
        },
        "components": {
            name: {p.name: p.default for p in definition.parameters}
            for name, definition in project.components.items()
        },
        "libraries": {name: list(lib.components) for name, lib in project.libraries.items()},
        "problems": project.validate(),
    }


def _component_info(project: Project, component: str, as_json: bool) -> int:
    """One component: its parameters (default, limits, what they are), points and ports."""
    found = project.definition(project.qualify(component))
    if found is None:
        raise ValueError(f"'{component}' is an imported cell: it has no parameters")
    definition, _ = found
    parameters = {
        p.name: {
            "default": p.default,
            "min": p.min,
            "max": p.max,
            "internal": p.internal,
            "description": p.description,
        }
        for p in definition.parameters
    }
    if as_json:
        data = {
            "name": component,
            "description": definition.description,
            "parameters": parameters,
            "points": [point.name for point in definition.points],
            "ports": [port.name for port in definition.ports],
        }
        print(json.dumps(data, indent=2))
        return 0
    print(
        f"component {component}" + (f": {definition.description}" if definition.description else "")
    )
    for name, p in parameters.items():
        definition_of = next(d for d in definition.parameters if d.name == name)
        limits = ", ".join(
            f"{sign} {_number(value)}"
            for sign, value in (
                (">" if definition_of.min_exclusive else "≥", p["min"]),
                ("<" if definition_of.max_exclusive else "≤", p["max"]),
            )
            if value is not None
        )
        extra = " (derived)" if p["internal"] else ""
        print(
            f"  {name} = {_number(p['default'])}{extra}"
            + (f" [{limits}]" if limits else "")
            + (f": {p['description']}" if p["description"] else "")
        )
    if definition.points:
        print(f"points: {', '.join(point.name for point in definition.points)}")
    if definition.ports:
        print(f"ports: {', '.join(port.name for port in definition.ports)}")
    return 0


def _number(value: float | str) -> str:
    return f"{value:g}" if isinstance(value, float | int) else str(value)


def _check(args: argparse.Namespace) -> int:
    project = load(args.project)
    name = _component(project, args.component)
    values = _values(project, name, args.set)
    checked = rules.checked_geometry(project, name, values)
    violations = rules.check(project, checked, name)
    violations = rules.name_shapes(
        violations, project, name, rules.records_of(project, name, values)
    )
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "rule": v.rule,
                        "kind": v.kind,
                        "severity": v.severity,
                        "layer": v.layer,
                        "message": v.message,
                        "bbox_um": v.bbox_um,
                        "measured_um": v.measured,
                        "shapes": list(v.shapes),
                        "values": v.values,
                        "waived": v.waived or None,
                    }
                    for v in violations
                ],
                indent=2,
            )
        )
    else:
        for v in violations:
            where = ""
            if v.bbox_um:
                x0, y0, x1, y1 = v.bbox_um
                where = f" at ({(x0 + x1) / 2:.3f}, {(y0 + y1) / 2:.3f}) µm"
            if v.shapes:
                where += f" on {', '.join(v.shapes)}"
            state = f"waived ({v.waived})" if v.waived else v.severity
            print(f"{state}: {v.rule} {v.layer}: {v.message}{where}")
        errors, warnings = len(rules.errors(violations)), len(rules.warnings(violations))
        waived = len(violations) - len(rules.open_violations(violations))
        print(f"{errors} error(s), {warnings} warning(s), {waived} waived", file=sys.stderr)
    failing = rules.open_violations(violations) if args.strict else rules.errors(violations)
    return 1 if failing else 0


def _rule_kinds(args: argparse.Namespace) -> int:
    for name, cls in sorted(rules.available_rule_kinds().items()):
        print(f"{name:16} {cls.title}  layers: {', '.join(cls.roles)}")
        for option in cls.parameters:
            unit = f" {option.suffix.strip()}" if option.suffix.strip() else ""
            print(f"    {option.name}={option.default}{unit}")
            if option.help:
                print(f"        {option.help}")
    return 0


def _export(args: argparse.Namespace) -> int:
    format_name = args.format or format_for(args.output)
    options = _options(format_name, args.option)
    project = load(args.project)
    params = _values(project, args.component, args.set)
    name = _component(project, args.component)
    print(f"building {name}…", file=sys.stderr, flush=True)  # a big design takes a while
    build = _build(project, args.component, params)
    print(f"writing {args.output}…", file=sys.stderr, flush=True)
    with cell_name_notes() as notes:
        path, written = export_with_report(
            project,
            args.output,
            format_name=format_name,
            geometry=build.geometry,
            component=args.component,
            params=params,
            options=options,
            build=build,
        )
    print(f"wrote {path}")
    for note in notes:
        print(f"warning: {note}", file=sys.stderr)
    changes = written.describe() if hasattr(written, "describe") else []
    if changes:
        print("snapping to the grid changed the shape:")
        for line in changes:
            print(f"  {line}")
    return 0


def _options(format_name: str, assignments: list[str]) -> dict[str, object]:
    declared = {o.name: o for o in options_of(exporter_class(format_name))}
    options: dict[str, object] = {}
    for assignment in assignments:
        name, sep, value = assignment.partition("=")
        name = name.strip()
        if not sep or not name:
            raise ValueError(f"--option expects NAME=VALUE, got '{assignment}'")
        if name not in declared and f"{name}_um" in declared:
            name = f"{name}_um"  # grid, chord: as the process calls them
        if name not in declared:
            known = ", ".join(declared) or "none"
            raise ValueError(f"'{format_name}' has no option '{name}' (its options: {known})")
        options[name] = declared[name].parse(value.strip())
    return options


def _formats(args: argparse.Namespace) -> int:
    for name, cls in sorted(available_exporters().items()):
        print(f"{name:8} {cls.file_extension:6} {title_of(cls)}")
        for option in options_of(cls):
            default = option.default if option.default != "" else '""'
            unit = f" {option.suffix.strip()}" if option.suffix.strip() else ""
            if option.name in PROCESS_DEFAULTS:  # what export uses unless it is given
                field = option.name.removesuffix("_um")
                print(f"    {option.name}={default}{unit} (default: the process's {field})")
                continue
            print(f"    {option.name}={default}{unit}")
            if option.help:
                print(f"        {option.help}")
    return 0


def _convert(args: argparse.Namespace) -> int:
    project = load(args.source)
    if is_document(args.target) and args.target.exists():
        raise FileExistsError(f"{args.target} already exists")
    if not is_document(args.target) and (args.target / "project.yaml").exists():
        raise FileExistsError(f"{args.target} already contains a project")
    save(project, args.target)
    print(f"converted {args.source} -> {args.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
