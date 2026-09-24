"""Importing layouts: one cell of a GDS or OASIS file, or the geometry in a
geometry document (.json, .xml, .mat: see :mod:`mems_sketch.storage.document`),
as a read-only component (see :mod:`mems_sketch.core.imports`).

A geometry document is kept as an OASIS file with one cell, ``TOP``, and its
layers' names and GDS numbers; a newer version of the document re-imports
like a newer GDS file."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import klayout.db as kdb

from mems_sketch.core.component import DBU_UM, Geometry, is_builtin
from mems_sketch.core.imports import (
    ImportedCell,
    cells,
    gds_layers,
    layer_key,
    layer_names,
    parse_layer_key,
    read_layout,
)
from mems_sketch.core.process import Layer
from mems_sketch.core.project import Project
from mems_sketch.editing.commands import Commands
from mems_sketch.editing.naming import fresh_name
from mems_sketch.storage.document import is_document, read_geometry

DOCUMENT_CELL = "TOP"  # the one cell a geometry document becomes


class ImportEdits(Commands):
    """The project's imported cells; each command is one undo step."""

    def add(
        self,
        path: str | Path,
        cell: str | None = None,
        name: str | None = None,
        layers: dict[str, str] | None = None,
    ) -> str:
        """Import ``cell`` (default: the first top cell) of the GDS file at ``path``
        as a component (default name: the file's), returns its name.

        ``layers`` maps GDS layers (``"5/0"``) to project layers; by default
        each goes to the project layer with the same GDS numbers, and one that
        has none gets a new layer (``gds5_0``). Mapped to ``""``, a layer is
        left out.
        """
        path = Path(path)
        data, file_name = self.read(path)
        cell = cell or cells(data)[0]
        name = name or self.suggested_name(path)
        self._check_name(name)
        mapping = self.default_layers(data, cell) if layers is None else dict(layers)
        file = self._file_name(file_name, data)

        def change(project: Project) -> None:
            for gds, layer in mapping.items():
                if layer and layer not in project.layers:
                    number, datatype = parse_layer_key(gds)
                    project.add_layer(Layer(layer, number, datatype))
            project.imports[name] = ImportedCell(
                name=name,
                file=file,
                cell=cell,
                layers={k: v for k, v in mapping.items() if v},
                data=data,
            )

        self.session.edit(f"Import {name}", change)
        return name

    def update(self, name: str, /, **fields: Any) -> None:
        """Change an import's ``cell``, ``layers`` or ``description``."""
        unknown = set(fields) - {"cell", "layers", "description"}
        if unknown:
            raise ValueError(f"an import has no {', '.join(sorted(unknown))}")

        def change(project: Project) -> None:
            imported = project.imports[name]
            for key, value in fields.items():
                setattr(imported, key, value)
            if imported.cell not in cells(imported.data):
                raise ValueError(f"'{imported.file}' has no cell '{imported.cell}'")

        self.session.edit(f"Edit import {name}", change)

    def reimport(self, name: str, path: str | Path) -> None:
        """Take a new version of the file: every placement of ``name`` follows. The
        cell and layer mapping stay; new GDS layers are mapped by their numbers."""
        data, _ = self.read(path)
        imported = self.session.project.imports[name]
        if imported.cell not in cells(data):
            raise ValueError(f"the new file has no cell '{imported.cell}'")
        defaults = self.default_layers(data, imported.cell)
        mapping = {**defaults, **imported.layers}

        def change(project: Project) -> None:
            target = project.imports[name]
            for gds, layer in mapping.items():
                if layer not in project.layers:
                    number, datatype = parse_layer_key(gds)
                    project.add_layer(Layer(layer, number, datatype))
            target.data, target.layers = data, mapping

        self.session.edit(f"Re-import {name}", change)

    def remove(self, name: str) -> None:
        users = self.session.project.users(name)
        if users:
            raise ValueError(f"'{name}' is still placed in: {', '.join(users)}")
        self.session.edit(f"Remove import {name}", lambda p: p.imports.pop(name))

    # -- helpers -----------------------------------------------------------------

    def read(self, path: str | Path) -> tuple[bytes, str]:
        """The layout to import from ``path`` (GDS or OASIS bytes) and the name to
        keep it under: the file itself, or a geometry document as OASIS."""
        path = Path(path)
        if is_document(path):
            geometry, numbers, _ = read_geometry(path)
            return self._as_oasis(geometry, numbers), f"{path.stem}.oas"
        data = path.read_bytes()
        read_layout(data)  # a readable file, or a clear error
        return data, path.name

    def _as_oasis(self, geometry: Geometry, numbers: dict[str, tuple[int, int] | None]) -> bytes:
        """The geometry as an OASIS file: layers keep their names, and get their GDS
        numbers from the document, else from the project layer of that name, else
        numbers nothing else uses."""
        taken = {n for n in numbers.values() if n is not None}
        taken |= {(ly.gds_layer, ly.gds_datatype) for ly in self.project.layers.values()}
        free = max((number for number, _ in taken), default=0) + 1
        layout = kdb.Layout()
        layout.dbu = DBU_UM
        top = layout.create_cell(DOCUMENT_CELL)
        for name, region in geometry.layers.items():
            gds = numbers.get(name)
            if gds is None and name in self.project.layers:
                layer = self.project.layers[name]
                gds = (layer.gds_layer, layer.gds_datatype)
            if gds is None:
                gds, free = (free, 0), free + 1
            top.shapes(layout.layer(kdb.LayerInfo(gds[0], gds[1], name))).insert(region)
        options = kdb.SaveLayoutOptions()
        options.format = "OASIS"
        return bytes(layout.write_bytes(options))

    def suggested_name(self, path: str | Path) -> str:
        """A free component name made from the file's name (``Pad frame`` -> ``pad_frame``)."""
        return self._free_name(_identifier(Path(path).stem))

    def default_layers(self, data: bytes, cell: str) -> dict[str, str]:
        """Each GDS layer of ``cell`` to the project layer with its GDS numbers, or a
        new layer's name (``gds5_0``) when there is none."""
        by_numbers = {(ly.gds_layer, ly.gds_datatype): n for n, ly in self.project.layers.items()}
        names = layer_names(data)
        result = {}
        for gds in gds_layers(data, cell):
            named = names.get(gds, "")
            new = named if named.isidentifier() and named not in self.project.layers else ""
            result[layer_key(gds)] = by_numbers.get(gds) or new or f"gds{gds[0]}_{gds[1]}"
        return result

    @property
    def project(self) -> Project:
        return self.session.project

    def _check_name(self, name: str) -> None:
        if not name.isidentifier():
            raise ValueError(f"'{name}' is not a valid component name")
        if name in self.project.components or name in self.project.imports or is_builtin(name):
            raise ValueError(f"a component named '{name}' already exists")

    def _free_name(self, base: str) -> str:
        taken = set(self.project.components) | set(self.project.imports)
        if base not in taken and not is_builtin(base):
            return base
        return fresh_name(base, taken)

    def _file_name(self, wanted: str, data: bytes) -> str:
        """``wanted``, unless another import keeps a different file under that name."""
        files = {i.file: i.data for i in self.project.imports.values()}
        stem, suffix = Path(wanted).stem, Path(wanted).suffix or ".gds"
        candidate, number = f"{stem}{suffix}", 1
        while candidate in files and files[candidate] != data:
            number += 1
            candidate = f"{stem}_{number}{suffix}"
        return candidate


def _identifier(text: str) -> str:
    """A component name from a file name: ``Pad frame-v2`` -> ``pad_frame_v2``."""
    name = re.sub(r"\W+", "_", text).strip("_").lower() or "imported"
    return f"cell_{name}" if name[0].isdigit() else name
