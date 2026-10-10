"""Project folders: the source of truth, meant to live in git.

Layout (the names of files and folders are only where things are; what they
are called is in the files)::

    my_project/
      project.yaml          the manifest: format, name, top component (null for a
                            library), libraries, the process used (and the
                            project's changes to a library's), processes,
                            shared components, imported cells
      processes/main/process.yaml
      components/
        top/component.yaml  one folder per component
        comb/component.yaml
        comb/finger/component.yaml   private to comb (comb lists it)
      imports/
        padframe.gds        a copy of each imported file (see core/imports.py)

``project.yaml``::

    format: mems-sketch/2
    name: resonator
    top: top
    libraries: {std: ../mems-std-lib}
    process: std.polymumps            # or one of its own: main
    overrides:                        # changes to a library's process (PRJ-8)
      constants: {undercut: 3}
      rules:
        poly1_min_width: {value: 1.8, reason: test structures}   # a change
        pads_space: {kind: min_space, layers: [metal], value: 5}  # an added rule
    processes: {main: processes/main}
    components: {top: components/top, comb: components/comb}
    imports:
      padframe: {file: padframe.gds, cell: FRAME, layers: {1/0: device, 5/0: metal}}

A library is a project folder too, usually without a top component: its
components and processes are placed and used as ``std.name``. Components are
written as in :mod:`mems_sketch.storage.component_format`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mems_sketch.core.imports import ImportedCell
from mems_sketch.core.process import (
    RULE_FIELDS,
    Layer,
    Level,
    Process,
    Rule,
    changes_between,
    check_levels,
    with_changes,
)
from mems_sketch.core.project import MAIN_PROCESS, Library, Project
from mems_sketch.core.user_component import ComponentDef
from mems_sketch.storage import yaml_format
from mems_sketch.storage.component_format import component_data, component_from_data

FORMAT = "mems-sketch/2"
PROJECT_FILE = "project.yaml"
PROCESS_FILE = "process.yaml"
COMPONENT_FILE = "component.yaml"
COMPONENTS_DIR = "components"
PROCESSES_DIR = "processes"
IMPORTS_DIR = "imports"


class ProjectFormatError(ValueError):
    pass


# -- saving ----------------------------------------------------------------


def save_project(project: Project, folder: str | Path) -> Path:
    """Write ``project`` into ``folder``. Unchanged files are not rewritten; files
    of components and processes the project no longer has are removed."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    libraries = {name: _relative(lib.path, folder) for name, lib in project.libraries.items()}
    manifest = manifest_data(project, libraries)
    manifest["processes"] = {name: f"{PROCESSES_DIR}/{name}" for name in project.processes}
    manifest["components"] = {
        name: f"{COMPONENTS_DIR}/{name}" for name in project.components if "/" not in name
    }
    if project.imports:
        manifest["imports"] = imports_data(project)
    _write(folder / PROJECT_FILE, yaml_format.dump(_without_empty(manifest)))
    _save_imports(project, folder / IMPORTS_DIR)
    written = set()
    for name, process in project.processes.items():
        path = folder / PROCESSES_DIR / name / PROCESS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        _write(path, yaml_format.dump(process_data(process)))
        written.add(path)
    for name, definition in project.components.items():
        path = folder / COMPONENTS_DIR / name / COMPONENT_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        private = {child: child for child in private_names(project.components, name)}
        _write(path, yaml_format.dump(component_data(definition, private)))
        written.add(path)
    for directory, file in ((COMPONENTS_DIR, COMPONENT_FILE), (PROCESSES_DIR, PROCESS_FILE)):
        for stale in (folder / directory).rglob(file):
            if stale not in written:
                stale.unlink()
        for empty in sorted((folder / directory).rglob("*"), reverse=True):  # deepest first
            if empty.is_dir() and not any(empty.iterdir()):
                empty.rmdir()
    return folder


def manifest_data(project: Project, libraries: dict[str, str]) -> dict[str, Any]:
    """The manifest's name, top, libraries, process and changes to it."""
    data: dict[str, Any] = {"format": FORMAT, "name": project.name, "top": project.top}
    data["libraries"] = libraries
    data["process"] = project.process_name
    base = project.base_process
    if base is not None:
        constants, changed, added = changes_between(base, project.process)
        rules = {name: yaml_format.to_data(c) for name, c in changed.items()}
        rules |= {name: rule_data(rule) for name, rule in added.items()}
        for name, entry in rules.items():
            if project.reasons.get(name):
                entry["reason"] = project.reasons[name]
        data["overrides"] = _without_empty(
            {"constants": yaml_format.to_data(constants), "rules": rules}
        )
    return data


def private_names(components: dict[str, ComponentDef], owner: str) -> list[str]:
    """The short names of the components private to ``owner`` (``comb``: ``finger``)."""
    prefix = owner + "/"
    return [
        n[len(prefix) :] for n in components if n.startswith(prefix) and "/" not in n[len(prefix) :]
    ]


def _without_empty(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v not in ({}, [], None) or k == "top"}


# -- the pieces, as plain data (also for one-file documents: storage/document.py)


def imports_data(project: Project) -> dict[str, Any]:
    """The imported cells, without the files' content."""
    return {
        name: {
            "file": imported.file,
            "cell": imported.cell,
            "layers": dict(sorted(imported.layers.items())),
            **({"description": imported.description} if imported.description else {}),
        }
        for name, imported in sorted(project.imports.items())
    }


def imported_from_data(name: str, entry: dict[str, Any], data: bytes) -> ImportedCell:
    return ImportedCell(
        name=name,
        file=str(entry["file"]),
        cell=str(entry["cell"]),
        layers={str(k): str(v) for k, v in (entry.get("layers") or {}).items()},
        description=str(entry.get("description", "")),
        data=data,
    )


def process_data(process: Process) -> dict[str, Any]:
    """A process as written (``process.yaml``)."""
    data: dict[str, Any] = {}
    if process.description:
        data["description"] = process.description
    if process.constants:
        data["constants"] = yaml_format.to_data(process.constants)
    data["layers"] = {
        layer.name: {"gds": [layer.gds_layer, layer.gds_datatype]}
        for layer in process.layers.values()
    }
    if process.levels:
        data["levels"] = [
            {"layer": level.layer, **({"roles": dict(level.roles)} if level.roles else {})}
            for level in process.levels
        ]
    if process.default_level is not None:
        data["default_level"] = process.default_level
    if process.rules:
        data["rules"] = {rule.name: rule_data(rule) for rule in process.rules.values()}
    return data


def process_from_data(data: dict[str, Any]) -> Process:
    """A process as read."""
    if not isinstance(data, dict):
        raise ProjectFormatError("a process is a map of its fields")
    layers = {}
    for name, entry in (data.get("layers") or {}).items():
        gds = (entry or {}).get("gds", [0, 0])
        layers[str(name)] = Layer(
            name=str(name),
            gds_layer=int(gds[0]),
            gds_datatype=int(gds[1]) if len(gds) > 1 else 0,
        )
    levels = [
        Level(
            layer=str(entry["layer"]),
            roles={str(k): str(v) for k, v in (entry.get("roles") or {}).items()},
        )
        for entry in data.get("levels") or []
    ]
    default_level = data.get("default_level")
    default_level = None if default_level is None else str(default_level)
    try:
        check_levels(levels, default_level, layers)
    except ValueError as error:
        raise ProjectFormatError(str(error)) from None
    return Process(
        layers=layers,
        constants={str(k): _value(v) for k, v in (data.get("constants") or {}).items()},
        rules={str(n): rule_from_data(n, e or {}) for n, e in (data.get("rules") or {}).items()},
        levels=levels,
        default_level=default_level,
        description=str(data.get("description", "")),
    )


def apply_overrides(project: Project, overrides: dict[str, Any]) -> None:
    """Use the project's library process with its changes (the manifest's ``overrides``)."""
    base = project.base_process
    if base is None:
        if overrides:
            raise ProjectFormatError("only a library's process can have overrides")
        return
    changed, added, reasons = {}, {}, {}
    for name, entry in (overrides.get("rules") or {}).items():
        entry = dict(entry or {})
        if entry.get("reason"):
            reasons[str(name)] = str(entry["reason"])
        entry.pop("reason", None)
        if "kind" in entry:
            added[str(name)] = rule_from_data(name, entry)
        else:
            changed[str(name)] = {str(k): _value(v) for k, v in entry.items()}
    constants = {str(k): _value(v) for k, v in (overrides.get("constants") or {}).items()}
    try:
        project.process = with_changes(base, constants, changed, added)
    except ValueError as error:
        raise ProjectFormatError(f"overrides of process {project.process_name}: {error}") from None
    project.reasons = reasons


def _value(value: Any) -> Any:
    """A value as read: numbers as floats; text, yes/no and lists as they are."""
    if isinstance(value, bool | str | list):
        return value
    return float(value)


def rule_data(rule: Rule) -> dict[str, Any]:
    """A rule as written: its kind and layers, then its values as keys of their
    own; severity, enabled and message only when not the default."""
    entry: dict[str, Any] = {"kind": rule.kind, "layers": list(rule.layers)}
    entry.update(yaml_format.to_data(rule.values))
    if rule.severity != "error":
        entry["severity"] = rule.severity
    if not rule.enabled:
        entry["enabled"] = False
    if rule.message:
        entry["message"] = rule.message
    return entry


def rule_from_data(name: str, entry: dict[str, Any]) -> Rule:
    values = {str(k): _value(v) for k, v in entry.items() if k not in RULE_FIELDS}
    return Rule(
        name=str(name),
        kind=str(entry.get("kind", "")),
        layers=[str(n) for n in entry.get("layers") or []],
        values=values,
        severity=str(entry.get("severity", "error")),
        enabled=bool(entry.get("enabled", True)),
        message=str(entry.get("message", "")),
    )


def _save_imports(project: Project, imports_dir: Path) -> None:
    """The imported files, each written once, and none that nothing uses any more."""
    files = {imported.file: imported.data for imported in project.imports.values()}
    if files:
        imports_dir.mkdir(exist_ok=True)
    for file, data in files.items():
        path = imports_dir / file
        if not (path.is_file() and path.read_bytes() == data):
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
    if imports_dir.is_dir():
        for stale in imports_dir.iterdir():
            if stale.is_file() and stale.name not in files:
                stale.unlink()
        if not any(imports_dir.iterdir()):
            imports_dir.rmdir()


def _write(path: Path, text: str) -> None:
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _relative(path: Path | None, folder: Path) -> str:
    if path is None:
        raise ValueError("a library without a folder cannot be saved in a project")
    try:
        return os.path.relpath(path.resolve(), folder.resolve()).replace(os.sep, "/")
    except ValueError:  # different drive on Windows
        return str(path.resolve())


# -- loading ---------------------------------------------------------------


def project_folder(path: str | Path) -> Path:
    path = Path(path)
    return path.parent if path.name == PROJECT_FILE else path


def load_project(path: str | Path, libraries_from: str | Path | None = None) -> Project:
    """Load a project from its folder or its ``project.yaml``.

    Libraries are found relative to ``libraries_from`` if given (a copy of a
    project, e.g. an earlier version from git, uses the real project's)."""
    folder = project_folder(path)
    base = Path(libraries_from) if libraries_from is not None else folder
    manifest = _manifest(folder)
    libraries = {
        str(name): load_library(str(name), Path(os.path.normpath(base / str(rel))))
        for name, rel in (manifest.get("libraries") or {}).items()
    }
    processes, components, notes = _contents(folder, manifest)
    top = manifest.get("top")  # null: a library, with no top component
    if top is not None and top not in components:
        raise ProjectFormatError(f"the top component '{top}' is not listed in {PROJECT_FILE}")
    process_name = str(manifest.get("process") or MAIN_PROCESS)
    if "." not in process_name and process_name not in processes:
        raise ProjectFormatError(f"the process '{process_name}' is not listed in {PROJECT_FILE}")
    project = Project(
        name=str(manifest.get("name", folder.name)),
        components=components,
        top=top,
        libraries=libraries,
        imports=_load_imports(manifest.get("imports") or {}, folder / IMPORTS_DIR),
        processes=processes,
        process_name=process_name,
        load_notes=notes,
    )
    try:
        apply_overrides(project, manifest.get("overrides") or {})
    except ValueError as error:
        raise ProjectFormatError(str(error)) from None
    return project


def load_library(name: str, folder: str | Path) -> Library:
    """A project folder as a library: its components and processes, read-only."""
    folder = Path(folder)
    if not (folder / PROJECT_FILE).is_file():
        raise ProjectFormatError(
            f"library '{name}': {folder} is not a project or library folder (no {PROJECT_FILE})"
        )
    processes, components, _ = _contents(folder, _manifest(folder))
    return Library(name=name, components=components, path=folder, processes=processes)


def _manifest(folder: Path) -> dict[str, Any]:
    manifest = _read(folder / PROJECT_FILE)
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ProjectFormatError(f"{folder / PROJECT_FILE} is not a {FORMAT} project file")
    return manifest


def _contents(
    folder: Path, manifest: dict[str, Any]
) -> tuple[dict[str, Process], dict[str, ComponentDef], list[str]]:
    """The processes and components a manifest lists, and notes on component and
    process files it does not."""
    processes = {}
    for name, rel in (manifest.get("processes") or {}).items():
        path = folder / str(rel) / PROCESS_FILE
        try:
            processes[str(name)] = process_from_data(_read(path) or {})
        except ProjectFormatError as error:
            raise ProjectFormatError(f"{path}: {error}") from None

    def open_component(rel: Any, where: Path) -> tuple[Any, Path]:
        path = where / str(rel) / COMPONENT_FILE
        return _read(path), path.parent

    components, files = read_components(manifest.get("components") or {}, open_component, folder)
    files |= {
        folder / str(rel) / PROCESS_FILE for rel in (manifest.get("processes") or {}).values()
    }
    listed = {f.resolve() for f in files}
    notes = [
        f"{path.relative_to(folder).as_posix()} is not listed, so it was not loaded"
        for pattern in (COMPONENT_FILE, PROCESS_FILE)
        for path in sorted(folder.rglob(pattern))
        if path.resolve() not in listed and ".mems-sketch" not in path.parts
    ]
    return processes, components, notes


def read_components(
    entries: dict[str, Any], open_entry: Callable[[Any, Any], tuple[Any, Any]], root: Any
) -> tuple[dict[str, ComponentDef], set[Path]]:
    """The components ``entries`` list (shared ones by name) and their private ones,
    each read with ``open_entry(entry, where) -> (data, where its private ones are)``;
    and the component files read (for a folder)."""
    components: dict[str, ComponentDef] = {}
    files: set[Path] = set()

    def visit(name: str, entry: Any, where: Any) -> None:
        if not all(part.isidentifier() for part in name.split("/")):
            raise ProjectFormatError(f"'{name}' is not a valid component name")
        data, here = open_entry(entry, where)
        if isinstance(here, Path):
            files.add(here / COMPONENT_FILE)
        try:
            definition, private = component_from_data(name, data)
        except Exception as exc:
            where_text = here / COMPONENT_FILE if isinstance(here, Path) else f"component '{name}'"
            raise ProjectFormatError(f"{where_text}: {exc}") from exc
        components[name] = definition
        for child, child_entry in private.items():
            visit(f"{name}/{child}", child_entry, here)

    for name, entry in entries.items():
        if "/" in str(name):
            raise ProjectFormatError(f"'{name}': private components are listed by their owner")
        visit(str(name), entry, root)
    return components, files


def _load_imports(entries: dict, imports_dir: Path) -> dict[str, ImportedCell]:
    imports = {}
    for name, entry in entries.items():
        path = imports_dir / str(entry["file"])
        if not path.is_file():
            raise ProjectFormatError(f"imported file {path} is missing")
        imports[name] = imported_from_data(name, entry, path.read_bytes())
    return imports


def _read(path: Path) -> Any:
    try:
        return yaml_format.load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ProjectFormatError(f"missing {path}") from None
