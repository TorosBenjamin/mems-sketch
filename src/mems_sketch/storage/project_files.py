"""Project folders: the source of truth, meant to live in git.

Layout::

    my_project/
      project.yaml          format, name, top component (null for a library), libraries,
                            imported cells
      process.yaml          layers and process constants
      imports/
        padframe.gds        a copy of each imported file (see core/imports.py)
      components/
        top.yaml            one file per local component
        comb.yaml
        comb/
          finger.yaml       a component private to comb (named "finger" in the file)

A library is a folder of component files (either directly or in a
``components/`` subfolder), loaded read-only under the name given in
``project.yaml``::

    libraries:
      std: ../mems-std-lib

An imported cell is a component named in ``project.yaml``::

    imports:
      padframe: {file: padframe.gds, cell: FRAME, layers: {1/0: device, 5/0: metal}}
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mems_sketch.core.imports import ImportedCell
from mems_sketch.core.process import Layer, Process
from mems_sketch.core.project import Library, Project
from mems_sketch.core.user_component import ComponentDef
from mems_sketch.storage import yaml_format

FORMAT = "mems-sketch/1"
PROJECT_FILE = "project.yaml"
PROCESS_FILE = "process.yaml"
COMPONENTS_DIR = "components"
IMPORTS_DIR = "imports"


class ProjectFormatError(ValueError):
    pass


# -- saving ----------------------------------------------------------------


def save_project(project: Project, folder: str | Path) -> Path:
    """Write ``project`` into ``folder``. Unchanged files are not rewritten."""
    folder = Path(folder)
    components_dir = folder / COMPONENTS_DIR
    components_dir.mkdir(parents=True, exist_ok=True)
    libraries = {name: _relative(lib.path, folder) for name, lib in project.libraries.items()}
    header: dict[str, Any] = {"format": FORMAT, "name": project.name, "top": project.top}
    if libraries:
        header["libraries"] = libraries
    if project.imports:
        header["imports"] = {
            name: {
                "file": imported.file,
                "cell": imported.cell,
                "layers": dict(sorted(imported.layers.items())),
                **({"description": imported.description} if imported.description else {}),
            }
            for name, imported in sorted(project.imports.items())
        }
    _write(folder / PROJECT_FILE, yaml_format.dump(header))
    _save_imports(project, folder / IMPORTS_DIR)
    _write(folder / PROCESS_FILE, yaml_format.dump(_process_data(project.process)))
    for name, definition in project.components.items():
        path = components_dir / f"{name}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = yaml_format.to_data(definition)
        data["name"] = definition.short_name  # the folder gives the owner
        _write(path, yaml_format.dump(data))
    for stale in components_dir.rglob("*.yaml"):
        if _component_path(stale, components_dir) not in project.components:
            stale.unlink()
    for directory in sorted(components_dir.rglob("*"), reverse=True):  # deepest first
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    return folder


def _component_path(file: Path, components_dir: Path) -> str:
    """``components/comb/finger.yaml`` -> ``comb/finger``."""
    return file.relative_to(components_dir).with_suffix("").as_posix()


def _process_data(process: Process) -> dict[str, Any]:
    layers = {}
    for layer in process.layers.values():
        entry: dict[str, Any] = {"gds": [layer.gds_layer, layer.gds_datatype]}
        for key in ("min_width", "min_space"):
            value = getattr(layer, key)
            if value:
                entry[key] = yaml_format.to_data(value)
        layers[layer.name] = entry
    data: dict[str, Any] = {}
    if process.constants:
        data["constants"] = yaml_format.to_data(process.constants)
    data["layers"] = layers
    return data


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


def load_project(path: str | Path) -> Project:
    """Load a project from its folder or its ``project.yaml``."""
    folder = project_folder(path)
    header = _read(folder / PROJECT_FILE)
    if not isinstance(header, dict) or header.get("format") != FORMAT:
        raise ProjectFormatError(f"{folder / PROJECT_FILE} is not a {FORMAT} project file")
    process = _load_process(folder / PROCESS_FILE)
    components = _load_components(folder / COMPONENTS_DIR)
    libraries = {
        name: load_library(name, (folder / rel) if not Path(rel).is_absolute() else Path(rel))
        for name, rel in (header.get("libraries") or {}).items()
    }
    top = header.get("top", "top")  # null: a library, with no top component
    if top is not None and top not in components:
        raise ProjectFormatError(f"top component '{top}' has no file in {COMPONENTS_DIR}/")
    return Project(
        name=str(header.get("name", folder.name)),
        process=process,
        components=components,
        top=top,
        libraries=libraries,
        imports=_load_imports(header.get("imports") or {}, folder / IMPORTS_DIR),
    )


def _load_imports(entries: dict, imports_dir: Path) -> dict[str, ImportedCell]:
    imports = {}
    for name, entry in entries.items():
        path = imports_dir / str(entry["file"])
        if not path.is_file():
            raise ProjectFormatError(f"imported file {path} is missing")
        imports[name] = ImportedCell(
            name=name,
            file=str(entry["file"]),
            cell=str(entry["cell"]),
            layers={str(k): str(v) for k, v in (entry.get("layers") or {}).items()},
            description=str(entry.get("description", "")),
            data=path.read_bytes(),
        )
    return imports


def load_library(name: str, folder: str | Path) -> Library:
    folder = Path(folder)
    if not folder.is_dir():
        raise ProjectFormatError(f"library '{name}' not found at {folder}")
    source = folder / COMPONENTS_DIR if (folder / COMPONENTS_DIR).is_dir() else folder
    return Library(name=name, components=_load_components(source), path=folder)


def _load_process(path: Path) -> Process:
    if not path.is_file():
        return Process()
    data = _read(path) or {}
    layers = {}
    for name, entry in (data.get("layers") or {}).items():
        gds = entry.get("gds", [0, 0])
        layers[name] = Layer(
            name=name,
            gds_layer=int(gds[0]),
            gds_datatype=int(gds[1]) if len(gds) > 1 else 0,
            min_width=_optional_float(entry.get("min_width")),
            min_space=_optional_float(entry.get("min_space")),
        )
    constants = {
        k: (float(v) if isinstance(v, int | float) else str(v))
        for k, v in (data.get("constants") or {}).items()
    }
    return Process(layers=layers, constants=constants)


def _load_components(folder: Path) -> dict[str, ComponentDef]:
    """Every component file, private ones in their owner's folder (``comb/finger.yaml``)."""
    components = {}
    files = []
    pending = [folder] if folder.is_dir() else []
    while pending:  # only an owner's folder holds private components: comb/ beside comb.yaml
        current = pending.pop()
        for path in sorted(current.glob("*.yaml")):
            files.append(path)
            if path.with_suffix("").is_dir():
                pending.append(path.with_suffix(""))
    files.sort(key=lambda p: (len(p.parts), p))
    for path in files:
        try:
            definition = ComponentDef.model_validate(_read(path))
        except Exception as exc:
            raise ProjectFormatError(f"{path}: {exc}") from exc
        if definition.name != path.stem:
            raise ProjectFormatError(
                f"{path}: component name '{definition.name}' does not match the file name"
            )
        name = _component_path(path, folder)
        try:
            definition = ComponentDef.model_validate({**definition.model_dump(), "name": name})
        except Exception as exc:
            raise ProjectFormatError(f"{path}: {exc}") from exc
        components[name] = definition
    return components


def _read(path: Path) -> Any:
    try:
        return yaml_format.load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ProjectFormatError(f"missing {path}") from None


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)
