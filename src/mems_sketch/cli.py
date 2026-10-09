"""Command-line interface to the compiler: project files in, results out.

    mems-sketch-cli new     my_project
    mems-sketch-cli info    my_project
    mems-sketch-cli check   my_project [--component plate] [--set pitch=15] [--json]
    mems-sketch-cli export  my_project out.gds [--set pitch=15] [--option grid_um=0.005]
    mems-sketch-cli formats                                         (export formats, their options)
    mems-sketch-cli convert my_project design.json                  (and back; .xml .mat .yaml)

``check`` exits with status 1 when there are rule violations, so it can gate
CI. Errors exit with status 2. Nothing here depends on the GUI.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from mems_sketch.core.component import Geometry
from mems_sketch.core.project import Project, new_project
from mems_sketch.export.base import (
    available_exporters,
    export,
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
    except Exception as exc:  # noqa: BLE001 - reported as a CLI error
        print(f"error: {exc}", file=sys.stderr)
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
    new.set_defaults(handler=_new)

    info = commands.add_parser("info", help="list components, parameters and layers")
    info.add_argument("project", type=Path)
    info.set_defaults(handler=_info)

    check = commands.add_parser("check", help="run design-rule checks (exit 1 on violations)")
    _geometry_arguments(check)
    check.add_argument("--json", action="store_true", help="print violations as JSON")
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


def _geometry(project: Project, args: argparse.Namespace) -> Geometry:
    return project.render(args.component, _parameters(args.set))


def _new(args: argparse.Namespace) -> int:
    if (args.folder / "project.yaml").exists():
        raise FileExistsError(f"{args.folder} already contains a project")
    project = new_project(args.name or args.folder.name, library=args.library)
    save(project, args.folder)
    print(f"created {args.folder}")
    return 0


def _info(args: argparse.Namespace) -> int:
    project = load(args.project)
    kind = f"top: {project.top}" if project.top else "library, no top component"
    print(f"project {project.name} ({kind})")
    print("layers:")
    for layer in project.layers.values():
        rules_text = ", ".join(
            f"{k} {v:g}"
            for k, v in (
                ("min width", layer.min_width),
                ("min space", layer.min_space),
            )
            if v
        )
        print(f"  {layer.name:<12} gds {layer.gds_layer}/{layer.gds_datatype}  {rules_text}")
    if project.process.constants:
        print("process constants:")
        for name, value in project.process.constants.items():
            print(f"  process.{name} = {value}")
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


def _number(value: float | str) -> str:
    return f"{value:g}" if isinstance(value, float | int) else str(value)


def _check(args: argparse.Namespace) -> int:
    project = load(args.project)
    violations = rules.check(project, _geometry(project, args))
    if args.json:
        print(
            json.dumps(
                [
                    {"rule": v.rule, "layer": v.layer, "message": v.message, "bbox_um": v.bbox_um}
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
            print(f"{v.rule} {v.layer}: {v.message}{where}")
        print(f"{len(violations)} violation(s)", file=sys.stderr)
    return 1 if violations else 0


def _export(args: argparse.Namespace) -> int:
    format_name = args.format or format_for(args.output)
    options = _options(format_name, args.option)
    project = load(args.project)
    params = _parameters(args.set)
    path = export(
        project,
        args.output,
        format_name=format_name,
        geometry=project.render(args.component, params),
        component=args.component,
        params=params,
        options=options,
    )
    print(f"wrote {path}")
    return 0


def _options(format_name: str, assignments: list[str]) -> dict[str, object]:
    declared = {o.name: o for o in options_of(exporter_class(format_name))}
    options: dict[str, object] = {}
    for assignment in assignments:
        name, sep, value = assignment.partition("=")
        name = name.strip()
        if not sep or not name:
            raise ValueError(f"--option expects NAME=VALUE, got '{assignment}'")
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
            print(f"    {option.name}={default}{unit}")
            if option.help:
                print(f"        {option.help}")
    return 0


def _convert(args: argparse.Namespace) -> int:
    project = load(args.source)
    if not is_document(args.target) and (args.target / "project.yaml").exists():
        raise FileExistsError(f"{args.target} already contains a project")
    save(project, args.target)
    print(f"converted {args.source} -> {args.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
