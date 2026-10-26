"""Project folders: the source of truth, meant to live in git.

Layout (the names of files and folders are only where things are; what they
are called is in the files)::

    my_project/
      my_project.msketch    the manifest: format, name, top component (null for a
                            library), libraries, the process used (and the
                            project's changes to a library's), processes,
                            shared components, imported cells
      processes/main/main.msketch
      components/
        top/top.msketch     one folder per component
        comb/comb.msketch
        comb/finger/finger.msketch   private to comb (comb lists it)
      imports/
        padframe.gds        a copy of each imported file (see core/imports.py)

**One extension, ``.msketch``, for every file** (#139), so that each can be
double-clicked: what a file is, its contents say. A manifest starts with
``format:``; a component's file with ``kind: component``, a process's with
``kind: process``. They are YAML. Projects written before have
``project.yaml``, ``components/top/component.yaml`` and
``processes/main/process.yaml``: those still load, and saving keeps the names
a project has (:func:`use_msketch_names` renames them). A folder with both
names for one file is an error.

The manifest::

    format: mems-sketch/2
    name: resonator
    top: top
    libraries: {mems: bundled@1, std: ../mems-std-lib}   # bundled ones, and folders
    process: mems.surface             # or one of its own: main
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
components and processes are placed and used as ``std.name``. Its manifest
may have a ``description`` and a ``version`` (a whole number, raised when a
change could change designs using it). Components are written as in
:mod:`mems_sketch.storage.component_format`.

A library that ships with mems-sketch (:mod:`mems_sketch.libraries`) is listed
as ``bundled`` (``m: bundled:mems`` under another name), with the version the
project was saved with: ``mems: bundled@1``. Opening the project with another
version notes it.

**Categories:** a component may sit in folders below ``components/``
(``components/springs/serpentine/``): that is its category, where it is
shown; its name stays its key in the manifest. A category folder may hold a
``category.yaml`` with a ``title``, ``description`` and ``order``.
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
    is_color,
    with_changes,
)
from mems_sketch.core.project import FORMER_BUILTINS, MAIN_PROCESS, Library, Project
from mems_sketch.core.user_component import ComponentDef
from mems_sketch.storage import yaml_format
from mems_sketch.storage.component_format import component_data, component_from_data

FORMAT = "mems-sketch/2"
BUNDLED = "bundled"
CATEGORY_FILE = "category.yaml"
EXTENSION = ".msketch"  # every file a project writes (#139)
COMPONENT_KIND, PROCESS_KIND = "component", "process"  # the header of their files
# The names before #139, still read (and kept by a project that has them).
PROJECT_FILE = "project.yaml"
PROCESS_FILE = "process.yaml"
COMPONENT_FILE = "component.yaml"
COMPONENTS_DIR = "components"
PROCESSES_DIR = "processes"
IMPORTS_DIR = "imports"


class ProjectFormatError(ValueError):
    pass


class MissingLibraryError(ProjectFormatError):
    """A library the project lists is not where it says: kept outside the project's
    repository, say, and missing from a clone (#310). ``name`` is the library,
    ``folder`` where it was looked for."""

    def __init__(self, name: str, folder: Path) -> None:
        super().__init__(
            f"library '{name}': {folder} is not a project or library folder "
            f"(no *{EXTENSION} or {PROJECT_FILE} in it)"
        )
        self.name, self.folder = name, folder


# -- which file is which (#139) ----------------------------------------------------


def file_kind(path: str | Path) -> str | None:
    """What a project file is, by its contents: ``project`` (a manifest; a library
    is one too), ``component`` or ``process``; None for anything else."""
    path = Path(path)
    try:
        data = yaml_format.load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - not readable as YAML: not one of ours
        return None
    if not isinstance(data, dict):
        return None
    if str(data.get("format", "")).startswith("mems-sketch/") and "kind" not in data:
        return "project"  # (of any version: loading says when it is not this one)
    if data.get("kind") in (COMPONENT_KIND, PROCESS_KIND):
        return str(data["kind"])
    if path.name == COMPONENT_FILE:
        return COMPONENT_KIND
    if path.name == PROCESS_FILE:
        return PROCESS_KIND
    return None


def manifest_files(folder: str | Path) -> list[Path]:
    """The manifests in ``folder``: its ``project.yaml`` and the ``.msketch`` files
    that are manifests (there should be one)."""
    folder = Path(folder)
    found = [folder / PROJECT_FILE] if (folder / PROJECT_FILE).is_file() else []
    if folder.is_dir():
        found += sorted(p for p in folder.glob(f"*{EXTENSION}") if file_kind(p) == "project")
    return found


def manifest_file(folder: str | Path) -> Path:
    """The project's manifest in ``folder``; an error when there is none, or more
    than one."""
    folder = Path(folder)
    found = manifest_files(folder)
    if not found:
        raise ProjectFormatError(f"{folder} has no project file (*{EXTENSION} or {PROJECT_FILE})")
    if len(found) > 1:
        names = " and ".join(p.name for p in found)
        raise ProjectFormatError(f"{folder} has {names}: keep one project file")
    return found[0]


def is_project_folder(folder: str | Path) -> bool:
    """Whether ``folder`` holds a project (or library): a manifest of either name."""
    return bool(manifest_files(folder))


def item_file(directory: Path, old_name: str) -> Path:
    """The file of a component or process in ``directory``: ``<directory>.msketch``
    (or, in a folder renamed since, its one ``.msketch`` file of that kind), or
    ``old_name`` (``component.yaml``) for one written before #139; an error when
    there are two. When there is none, ``<directory>.msketch`` (what to write)."""
    kind = COMPONENT_KIND if old_name == COMPONENT_FILE else PROCESS_KIND
    new, old = directory / f"{directory.name}{EXTENSION}", directory / old_name
    found = [new] if new.is_file() else []
    if not found and directory.is_dir():
        found = [p for p in sorted(directory.glob(f"*{EXTENSION}")) if file_kind(p) == kind]
    if old.is_file():
        found.append(old)
    if len(found) > 1:
        names = " and ".join(p.name for p in found)
        raise ProjectFormatError(f"{directory} has {names}: keep one {kind} file")
    return found[0] if found else new


def _uses_msketch(folder: Path) -> bool:
    """Whether a project in ``folder`` is written with ``.msketch`` names: a new one
    is; one with a ``project.yaml`` keeps its names."""
    return not (folder / PROJECT_FILE).is_file()


def _manifest_target(folder: Path) -> Path:
    """Where saving writes the manifest: the one there is, else ``<folder>.msketch``."""
    found = manifest_files(folder)
    if len(found) > 1:
        manifest_file(folder)  # the error
    return found[0] if found else folder / f"{folder.name}{EXTENSION}"


def use_msketch_names(folder: str | Path) -> list[tuple[Path, Path]]:
    """Rename a project's files to ``.msketch`` names (#139): the manifest to
    ``<folder>.msketch``, each component's and process's file to its folder's
    name, with their ``kind:`` header. Returns what was renamed (old, new)."""
    folder = Path(folder)
    old_manifest = folder / PROJECT_FILE
    if not old_manifest.is_file():
        return []
    manifest = _manifest(folder)
    moves: list[tuple[Path, Path, str | None]] = [
        (old_manifest, folder / f"{folder.name}{EXTENSION}", None)
    ]
    for rel in (manifest.get("processes") or {}).values():
        directory = folder / str(rel)
        moves.append(
            (directory / PROCESS_FILE, directory / f"{directory.name}{EXTENSION}", PROCESS_KIND)
        )
    for path in sorted((folder / COMPONENTS_DIR).rglob(COMPONENT_FILE)):
        moves.append((path, path.parent / f"{path.parent.name}{EXTENSION}", COMPONENT_KIND))
    for old, new, _ in moves:
        if old.is_file() and new.exists():
            raise ProjectFormatError(f"{new} exists already: nothing was renamed")
    renamed = []
    for old, new, kind in moves:
        if not old.is_file():
            continue
        if kind is None:
            os.replace(old, new)
        else:
            data = _read(old) or {}
            _write(new, yaml_format.dump({"kind": kind, **data}))
            old.unlink()
        renamed.append((old, new))
    return renamed


def relocate_library(project: str | Path, name: str, folder: str | Path) -> None:
    """Point the project in ``project`` (its folder or manifest) at ``folder`` for
    its library ``name``: written relative to the project, as saving does."""
    project = project_folder(project)
    folder = Path(folder)
    if not is_project_folder(folder):
        raise ProjectFormatError(
            f"{folder} is not a project or library folder (no *{EXTENSION} or {PROJECT_FILE})"
        )
    manifest = _manifest(project)
    libraries = manifest.get("libraries") or {}
    if name not in libraries:
        raise ProjectFormatError(f"the project lists no library '{name}'")
    libraries[name] = _relative(folder, project)
    manifest["libraries"] = libraries
    _write(manifest_file(project), yaml_format.dump(manifest))


# -- saving ----------------------------------------------------------------


def save_project(project: Project, folder: str | Path) -> Path:
    """Write ``project`` into ``folder``. Files whose contents did not change are
    not rewritten (a hand-edited file keeps its comments and layout); files of
    components, processes and imports the project had and no longer has are
    removed, and no others."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    owned = _owned(folder)
    libraries = {
        name: library_entry(lib) if lib.bundled else _relative(lib.path, folder)
        for name, lib in project.libraries.items()
    }
    manifest = manifest_data(project, libraries)
    manifest["processes"] = {name: f"{PROCESSES_DIR}/{name}" for name in project.processes}
    manifest["components"] = {
        name: component_folder(project.components, name)
        for name in project.components
        if "/" not in name
    }
    if project.imports:
        manifest["imports"] = imports_data(project)
    msketch = _uses_msketch(folder)
    _write(_manifest_target(folder), yaml_format.dump(_without_empty(manifest)))
    _save_imports(project, folder / IMPORTS_DIR, owned)
    written = set()
    for name, process in project.processes.items():
        directory = folder / PROCESSES_DIR / name
        path = _item_target(directory, PROCESS_FILE, msketch)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = process_data(process)
        _write(path, yaml_format.dump({"kind": PROCESS_KIND, **data} if _is_new(path) else data))
        written.add(path)
    for name, definition in project.components.items():
        directory = folder / component_folder(project.components, name)
        path = _item_target(directory, COMPONENT_FILE, msketch)
        path.parent.mkdir(parents=True, exist_ok=True)
        private = {child: child for child in private_names(project.components, name)}
        data = component_data(definition, private)
        _write(path, yaml_format.dump({"kind": COMPONENT_KIND, **data} if _is_new(path) else data))
        written.add(path)
    for directory, file in ((COMPONENTS_DIR, COMPONENT_FILE), (PROCESSES_DIR, PROCESS_FILE)):
        for stale in _item_files(folder / directory, file):
            if stale not in written and _is_in(stale, owned):
                stale.unlink()
        for empty in sorted((folder / directory).rglob("*"), reverse=True):  # deepest first
            if empty.is_dir() and not any(empty.iterdir()):
                empty.rmdir()
    return folder


def _item_target(directory: Path, old_name: str, msketch: bool) -> Path:
    """Where saving writes a component's or process's file: the one there is, else
    the project's kind of name."""
    found = item_file(directory, old_name)
    if found.is_file():
        return found
    return directory / f"{directory.name}{EXTENSION}" if msketch else directory / old_name


def _is_new(path: Path) -> bool:
    return path.suffix == EXTENSION


def _item_files(root: Path, old_name: str) -> list[Path]:
    """The component (or process) files below ``root``, of either name."""
    if not root.is_dir():
        return []
    kind = COMPONENT_KIND if old_name == COMPONENT_FILE else PROCESS_KIND
    found = list(root.rglob(old_name))
    found += [p for p in root.rglob(f"*{EXTENSION}") if file_kind(p) == kind]
    return sorted(found)


def component_folder(components: dict[str, ComponentDef], name: str) -> str:
    """Where a component's file goes: its category's folder, then its name (a
    private one inside its owner's folder)."""
    root = name.split("/")[0]
    category = components[root].category if root in components else ""
    return "/".join(p for p in (COMPONENTS_DIR, category, name) if p)


def written_by_project(path: str) -> bool:
    """Whether a file (its path in the project folder, with ``/``) is one a project
    writes: the manifest, a component's or a process's file, or an imported file."""
    parts = path.split("/")
    if len(parts) == 1:
        return parts[0] == PROJECT_FILE or parts[0].endswith(EXTENSION)
    own = len(parts) >= 3 and parts[-1] == f"{parts[-2]}{EXTENSION}"  # comb/comb.msketch
    if parts[0] == COMPONENTS_DIR:
        return parts[-1] == COMPONENT_FILE or own
    if parts[0] == PROCESSES_DIR:
        return parts[-1] == PROCESS_FILE or own
    return parts[0] == IMPORTS_DIR and len(parts) == 2


def library_entry(library: Library) -> str:
    """How a manifest lists a bundled library: ``bundled@1``, ``bundled:mems@1``."""
    text = BUNDLED if library.bundled == library.name else f"{BUNDLED}:{library.bundled}"
    return text if library.version is None else f"{text}@{library.version}"


def open_library(name: str, entry: Any, base: Path, notes: list[str]) -> Library:
    """The library a manifest lists as ``name``: a bundled one, or a folder
    relative to ``base``; a bundled one whose version is not the one the project
    was saved with is noted."""
    text = str(entry)
    if text == BUNDLED or text.startswith((BUNDLED + ":", BUNDLED + "@")):
        from mems_sketch.libraries import bundled_library

        spec, _, saved = text.partition("@")
        bundled = spec.partition(":")[2] or name
        try:
            library = bundled_library(bundled, name)
        except KeyError as error:
            raise ProjectFormatError(f"library '{name}': {error.args[0]}") from None
        if saved and library.version is not None and saved != str(library.version):
            notes.append(
                f"library '{name}' is version {library.version}; the project was saved "
                f"with version {saved}, so its components may have changed"
            )
        return library
    path = Path(text)
    return load_library(name, path if path.is_absolute() else Path(os.path.normpath(base / path)))


# What the built-in components are called now (they are components of the
# bundled ``mems`` library).
BUILTIN_COMPONENTS = {name: f"mems.{name}" for name in FORMER_BUILTINS}


def migrate_builtin_references(project: Project) -> None:
    """References to the former built-in components (``comb_drive``) placed by
    their bare names, made references to the ``mems`` library's (where nothing
    of the project's has that name); noted once."""
    from mems_sketch.libraries import bundled_library

    moved = _migrated(project.components)
    for library in project.libraries.values():
        moved |= _migrated(library.components)
    if moved:
        if "mems" not in project.libraries:
            project.libraries["mems"] = bundled_library("mems")
        names = ", ".join(sorted(moved))
        project.load_notes.append(
            f"the built-in components are in the bundled library 'mems' now: "
            f"placements were changed to {names}"
        )


def _migrated(components: dict[str, ComponentDef]) -> set[str]:
    from mems_sketch.core.shapes import RefShape, walk

    moved = set()
    for definition in components.values():
        context = definition.name.split("/")
        for shape in walk(definition.shapes):
            if not isinstance(shape, RefShape) or shape.component not in BUILTIN_COMPONENTS:
                continue
            owners = ["/".join([*context[:k], shape.component]) for k in range(len(context) + 1)]
            if any(candidate in components for candidate in owners):
                continue  # a component of its own
            shape.component = BUILTIN_COMPONENTS[shape.component]
            moved.add(shape.component)
    return moved


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
        layer.name: {
            "gds": [layer.gds_layer, layer.gds_datatype],
            **({"color": layer.color} if layer.color else {}),
        }
        for layer in process.layers.values()
    }
    if process.levels:
        data["levels"] = [
            {"layer": level.layer, **({"roles": dict(level.roles)} if level.roles else {})}
            for level in process.levels
        ]
    if process.default_level is not None:
        data["default_level"] = process.default_level
    if process.grid_um != Process.grid_um:
        data["grid"] = process.grid_um
    if process.chord_um != Process.chord_um:
        data["chord"] = process.chord_um
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
        color = (entry or {}).get("color")
        if color is not None and not is_color(color):
            raise ProjectFormatError(f"layer '{name}': colour {color!r} is not like '#4a90d9'")
        layers[str(name)] = Layer(
            name=str(name),
            gds_layer=int(gds[0]),
            gds_datatype=int(gds[1]) if len(gds) > 1 else 0,
            color=None if color is None else str(color).lower(),
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
        grid_um=_positive(data, "grid", Process.grid_um),
        chord_um=_positive(data, "chord", Process.chord_um),
    )


def _positive(data: dict[str, Any], key: str, default: float) -> float:
    """A positive length (µm) of a process, or the default when it is not given."""
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float) or not value > 0:
        raise ProjectFormatError(f"the process's {key} is a positive length in µm, not {value!r}")
    return float(value)


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
    if not isinstance(entry, dict):
        raise ProjectFormatError(f"rule '{name}': expected its fields (kind, layers, …)")
    values = {}
    for key, value in entry.items():
        if key in RULE_FIELDS:
            continue
        if value is None:  # "{value: 2, 5}" reads as a key "5" with no value
            raise ProjectFormatError(
                f"rule '{name}': '{key}' has no value (inside {{ }}, a comma starts a new "
                "key: put text with commas in quotes)"
            )
        try:
            values[str(key)] = _value(value)
        except (TypeError, ValueError):
            raise ProjectFormatError(
                f"rule '{name}': '{key}' must be a number, text, yes/no or a list, not {value!r}"
            ) from None
    return Rule(
        name=str(name),
        kind=str(entry.get("kind", "")),
        layers=[str(n) for n in entry.get("layers") or []],
        values=values,
        severity=str(entry.get("severity", "error")),
        enabled=bool(entry.get("enabled", True)),
        message=str(entry.get("message", "")),
    )


def _owned(folder: Path) -> set[Path]:
    """The folders and files the project already in ``folder`` lists (its
    components', processes' and imported files): the ones a save may remove.
    Empty when there is no project there, so nothing is removed."""
    try:
        manifest = _manifest(folder)
    except ProjectFormatError:
        return set()
    listed = [*(manifest.get("components") or {}).values()]
    listed += (manifest.get("processes") or {}).values()
    owned = {(folder / str(rel)).resolve() for rel in listed}
    for entry in (manifest.get("imports") or {}).values():
        if isinstance(entry, dict) and "file" in entry:
            owned.add((folder / IMPORTS_DIR / str(entry["file"])).resolve())
    return owned


def _is_in(path: Path, owned: set[Path]) -> bool:
    path = path.resolve()
    return any(path == o or o in path.parents for o in owned)


def _save_imports(project: Project, imports_dir: Path, owned: set[Path]) -> None:
    """The imported files, each written once, and none that nothing uses any more
    (of those the project listed)."""
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
            if stale.is_file() and stale.name not in files and _is_in(stale, owned):
                stale.unlink()
        if not any(imports_dir.iterdir()):
            imports_dir.rmdir()


def _write(path: Path, text: str) -> None:
    """Write ``text`` unless the file already says the same: the same text, or
    YAML with the same contents written differently (by hand: comments, ``0.10``)."""
    if path.is_file():
        old = path.read_text(encoding="utf-8")
        if old == text or _same_data(old, text):
            return
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _same_data(old: str, new: str) -> bool:
    try:
        return yaml_format.load(old) == yaml_format.load(new)
    except Exception:  # noqa: BLE001 - an unreadable file is rewritten
        return False


def _relative(path: Path | None, folder: Path) -> str:
    if path is None:
        raise ValueError("a library without a folder cannot be saved in a project")
    try:
        return os.path.relpath(path.resolve(), folder.resolve()).replace(os.sep, "/")
    except ValueError:  # different drive on Windows
        return str(path.resolve())


# -- loading ---------------------------------------------------------------


def project_folder(path: str | Path) -> Path:
    """The project folder ``path`` names: the folder, its manifest, or a component's
    or process's file in it (the folder up from there with a manifest)."""
    path = Path(path)
    if path.name == PROJECT_FILE or (path.suffix == EXTENSION and file_kind(path) == "project"):
        return path.parent
    if path.is_file() and file_kind(path) in (COMPONENT_KIND, PROCESS_KIND):
        for folder in path.parents:
            if is_project_folder(folder):
                return folder
        raise ProjectFormatError(f"{path} is not in a project folder")
    return path


def locate(path: str | Path) -> tuple[Path, str, str | None]:
    """What opening ``path`` opens (#139): (the project folder, what the file is,
    and the component or process it names, if it is one's file). A folder or a
    manifest is ``project``."""
    path = Path(path)
    folder = project_folder(path)
    kind = "project" if path.is_dir() else file_kind(path) or "project"
    if kind not in (COMPONENT_KIND, PROCESS_KIND):
        return folder, "project", None
    manifest = _manifest(folder)
    key = "components" if kind == COMPONENT_KIND else "processes"
    here = path.parent.resolve()

    def found(entries: dict, where: Path, prefix: str = "") -> str | None:
        for name, rel in entries.items():
            directory = (where / str(rel)).resolve()
            if directory == here:
                return f"{prefix}{name}"
            if kind == COMPONENT_KIND and here.is_relative_to(directory):
                data = _read(item_file(directory, COMPONENT_FILE)) or {}
                inner = found(data.get("private") or {}, directory, f"{prefix}{name}/")
                if inner is not None:
                    return inner
        return None

    return folder, kind, found(manifest.get(key) or {}, folder)


def load_project(path: str | Path, libraries_from: str | Path | None = None) -> Project:
    """Load a project from its folder, its manifest, or a file of one of its
    components or processes.

    Libraries are found relative to ``libraries_from`` if given (a copy of a
    project, e.g. an earlier version from git, uses the real project's)."""
    folder = project_folder(path)
    base = Path(libraries_from) if libraries_from is not None else folder
    manifest = _manifest(folder)
    processes, components, notes = _contents(folder, manifest)
    libraries = {
        str(name): open_library(str(name), entry, base, notes)
        for name, entry in (manifest.get("libraries") or {}).items()
    }
    top = manifest.get("top")  # null: a library, with no top component
    if top is not None and top not in components:
        raise ProjectFormatError(f"the top component '{top}' is not listed in the project file")
    process_name = str(manifest.get("process") or MAIN_PROCESS)
    if "." not in process_name and process_name not in processes:
        raise ProjectFormatError(f"the process '{process_name}' is not listed in the project file")
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
    migrate_builtin_references(project)
    try:
        apply_overrides(project, manifest.get("overrides") or {})
    except ValueError as error:
        raise ProjectFormatError(str(error)) from None
    return project


def load_library(name: str, folder: str | Path) -> Library:
    """A project folder as a library: its components and processes, read-only."""
    folder = Path(folder)
    if not is_project_folder(folder):
        raise MissingLibraryError(name, folder)
    manifest = _manifest(folder)
    processes, components, _ = _contents(folder, manifest)
    version = manifest.get("version")
    if version is not None and (isinstance(version, bool) or not isinstance(version, int)):
        raise ProjectFormatError(
            f"library '{name}': its version is a whole number, not {version!r}"
        )
    return Library(
        name=name,
        components=components,
        path=folder,
        processes=processes,
        description=str(manifest.get("description", "")),
        version=version,
        categories=_categories(folder / COMPONENTS_DIR),
    )


def _categories(components_dir: Path) -> dict[str, dict[str, Any]]:
    """The ``category.yaml`` files below a components folder, by category."""
    found = {}
    for path in sorted(components_dir.rglob(CATEGORY_FILE)) if components_dir.is_dir() else []:
        data = _read(path)
        if not isinstance(data, dict):
            raise ProjectFormatError(
                f"{path}: a category is a map of its title, description, order"
            )
        category = path.parent.relative_to(components_dir).as_posix()
        found[category] = {
            "title": str(data.get("title", "")),
            "description": str(data.get("description", "")),
            "order": data.get("order"),
        }
    return found


def _manifest(folder: Path) -> dict[str, Any]:
    if not manifest_files(folder) and not (folder / PROJECT_FILE).exists():
        raise ProjectFormatError(f"missing {folder / PROJECT_FILE}")  # nothing there at all
    path = manifest_file(folder)
    manifest = _read(path)
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ProjectFormatError(f"{path} is not a {FORMAT} project file")
    return manifest


def _contents(
    folder: Path, manifest: dict[str, Any]
) -> tuple[dict[str, Process], dict[str, ComponentDef], list[str]]:
    """The processes and components a manifest lists, and notes on component and
    process files it does not."""
    processes = {}
    process_files = set()
    for name, rel in (manifest.get("processes") or {}).items():
        path = item_file(folder / str(rel), PROCESS_FILE)
        process_files.add(path)
        try:
            processes[str(name)] = process_from_data(_without_kind(_read(path), path, PROCESS_KIND))
        except (ProjectFormatError, ValueError, TypeError) as error:  # say which file
            raise ProjectFormatError(f"{path}: {error}") from None

    def open_component(rel: Any, where: Path) -> tuple[Any, Path]:
        path = item_file(where / str(rel), COMPONENT_FILE)
        return _without_kind(_read(path), path, COMPONENT_KIND), path.parent

    entries = manifest.get("components") or {}
    components, files = read_components(entries, open_component, folder)
    for name, rel in entries.items():  # its category: the folders it is in
        parts = Path(str(rel)).parts
        if len(parts) > 2 and parts[0] == COMPONENTS_DIR and parts[-1] == str(name):
            components[str(name)].category = "/".join(parts[1:-1])
    files |= process_files
    listed = {f.resolve() for f in files}
    notes = [
        f"{path.relative_to(folder).as_posix()} is not listed, so it was not loaded"
        for directory, old_name in ((COMPONENTS_DIR, COMPONENT_FILE), (PROCESSES_DIR, PROCESS_FILE))
        for path in _item_files(folder / directory, old_name)
        if path.resolve() not in listed and ".mems-sketch" not in path.parts
    ]
    return processes, components, notes


def _without_kind(data: Any, path: Path, kind: str) -> Any:
    """A component's or process's file without its ``kind:`` header, which must say
    what is expected (a ``.msketch`` file must have it)."""
    if not isinstance(data, dict):
        return data
    found = data.get("kind")
    if found is None and path.suffix == EXTENSION:
        raise ProjectFormatError(f"{path} does not say what it is (kind: {kind})")
    if found is not None and found != kind:
        raise ProjectFormatError(f"{path} is a {found} file, not a {kind}'s")
    return {k: v for k, v in data.items() if k != "kind"}


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
            files.add(item_file(here, COMPONENT_FILE))
        try:
            definition, private = component_from_data(name, data)
        except Exception as exc:
            where_text = (
                item_file(here, COMPONENT_FILE) if isinstance(here, Path) else f"component '{name}'"
            )
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
