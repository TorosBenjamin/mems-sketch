"""What the MCP server's tools do, as plain Python over an :class:`EditSession`.

Every tool names things (components, shapes, parameters) rather than indexing
them, and reads and writes components in the file format, so what an
assistant reads is what it writes. Edits go through the session: one that
breaks a project that built before is rolled back and comes back as an error,
each can be undone, and library components are read-only. Errors are raised
as :class:`ValueError` with the message the editor would show; the server
turns them into tool errors.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

from mems_sketch.core import errors
from mems_sketch.core.shapes import NodePath, Shape, paths
from mems_sketch.core.shapes.registry import kind_class
from mems_sketch.core.user_component import ComponentDef, PointDef
from mems_sketch.editing import EditSession
from mems_sketch.engine import Build
from mems_sketch.export.base import export_with_report
from mems_sketch.mcp_server import kinds, reference
from mems_sketch.process import rules
from mems_sketch.storage import yaml_format
from mems_sketch.storage.component_format import (
    component_data,
    component_from_data,
    shapes_data,
    shapes_from_data,
)

MAX_VARIANTS = 24  # images in one render_variants


class DesignTools:
    """The tools, on one session (a new, unsaved project until one is opened)."""

    def __init__(self, session: EditSession | None = None) -> None:
        self.session = session or EditSession()

    # -- project -------------------------------------------------------------

    def open_project(self, path: str) -> dict[str, Any]:
        self.session.open(path)
        return self.project_summary()

    def new_project(self, folder: str, name: str, library: bool = False) -> dict[str, Any]:
        self.session.create(folder, name, library=library)
        return self.project_summary()

    def save(self, folder: str | None = None, replace: bool = False) -> str:
        return f"saved in {self.session.save(folder, replace=replace)}"

    def undo(self) -> str:
        if not self.session.can_undo():
            raise ValueError("there is nothing to undo")
        what = self.session.undo_text()
        self.session.undo()
        return f"undid: {what}"

    def redo(self) -> str:
        if not self.session.can_redo():
            raise ValueError("there is nothing to redo")
        what = self.session.redo_text()
        self.session.redo()
        return f"redid: {what}"

    def project_summary(self) -> dict[str, Any]:
        project = self.session.project
        return {
            "name": project.name,
            "folder": str(self.session.path) if self.session.path else None,
            "unsaved_changes": self.session.dirty,
            "top": project.default_component(),
            "components": sorted(project.components),
            "libraries": sorted(project.libraries),
            "process": project.process_name,
            "layers": list(project.process.stack_order()),
        }

    def list_components(self, library: str | None = None) -> dict[str, Any]:
        """The project's own components and each library's, with what they are."""
        project = self.session.project
        result: dict[str, Any] = {}
        if library is None:
            result["project"] = {
                "description": "this project's own components (editable)",
                "components": [_entry(n, d) for n, d in sorted(project.components.items())],
            }
        for name, found in sorted(project.libraries.items()):
            if library not in (None, name):
                continue
            result[name] = {
                "description": found.description,
                "components": [
                    _entry(f"{name}.{n}", d, found.category_title(d.category) if d.category else "")
                    for n, d in sorted(found.components.items())
                ],
            }
        if library is not None and library not in result:
            raise ValueError(f"there is no library '{library}' (there are: {_names(result)})")
        return result

    # -- reading -------------------------------------------------------------

    def get_component(self, component: str | None = None) -> str:
        """A component in the file format (YAML), as it would be written."""
        name = self._component(component)
        definition = self.session.definition_of(name)
        text = yaml_format.dump(component_data(definition))
        note = "" if name in self.session.project.components else "# read-only: a library's\n"
        return f"# {name}\n{note}{text}"

    def describe_kinds(self) -> str:
        """Every shape kind and modifier, one line each."""
        return kinds.index()

    def describe_kind(self, kind: str) -> str:
        """What a shape kind or modifier is, its fields and an example."""
        return kinds.describe(kind)

    def reference(self, topic: str) -> str:
        """A reference topic: expressions, components, points, layers."""
        return reference.reference(topic, self.session.project)

    # -- editing -------------------------------------------------------------

    def add_shape(self, component: str | None, name: str, shape: dict[str, Any]) -> str:
        """Add a top-level shape, written as in the file format (``{kind: rect, ...}``,
        or ``{ref: mems.anchor, ...}`` for a placed component)."""
        self._edit(component)
        if any(n == name for n, _ in self._named()):
            raise ValueError(f"there is already a shape '{name}'")
        self.session.nodes.add(_shape(name, shape))
        return f"added {name}"

    def update_shape(self, component: str | None, name: str, fields: dict[str, Any]) -> str:
        """Change fields of a shape (anywhere in the tree), as written in the file
        format; a field set to null goes back to its default."""
        if "name" in fields:
            raise ValueError("rename a shape with rename_shape")
        self._edit(component)
        path, shape = self._find(name)
        data = shapes_data([shape])[name]
        for key, value in fields.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        self.session.nodes.replace(path, _shape(name, data))
        return f"updated {name}"

    def rename_shape(self, component: str | None, name: str, new: str) -> str:
        """Rename a shape; what refers to it follows."""
        self._edit(component)
        self._find(name)
        self.session.edit(f"Rename {name}", lambda p: self.session.local(p).rename_shape(name, new))
        return f"renamed {name} to {new}"

    def delete_shape(self, component: str | None, name: str) -> str:
        self._edit(component)
        path, _ = self._find(name)
        self.session.nodes.remove([path])
        return f"deleted {name}"

    def add_modifier(
        self, component: str | None, shape: str, kind: str, fields: dict[str, Any] | None = None
    ) -> str:
        """Add a modifier (array, mirror, corners, ...) to the end of a shape's stack."""
        self._edit(component)
        path, _ = self._find(shape)
        index = self.session.modifiers.add(path, kind, **(fields or {}))
        return f"added {kind} to {shape} (modifier {index + 1})"

    def set_parameter(
        self,
        component: str | None,
        name: str,
        default: float | str,
        **limits: Any,
    ) -> str:
        """Create or change a parameter: its default and any of min, max,
        min_exclusive, max_exclusive, integer, internal, description."""
        self._edit(component)
        self.session.parameters.set(name, default, **{k: v for k, v in limits.items()})
        return f"set {name} = {default}"

    def set_point(
        self,
        component: str | None,
        name: str,
        at: str | None = None,
        x: float | str = 0.0,
        y: float | str = 0.0,
        description: str = "",
    ) -> str:
        """Create or change one of the component's own points."""
        self._edit(component)
        point = PointDef(name=name, at=at, x=x, y=y, description=description)

        def change(project) -> None:
            points = self.session.local(project).points
            for index, existing in enumerate(points):
                if existing.name == name:
                    points[index] = point
                    return
            points.append(point)

        self.session.edit(f"Set point {name}", change)
        return f"set point {name}"

    def place_component(
        self,
        component: str | None,
        name: str,
        place: str,
        params: dict[str, Any] | None = None,
        x: float | str = 0.0,
        y: float | str = 0.0,
        rotation: float | str = 0.0,
    ) -> str:
        """Place another component (``mems.comb_drive``, a project component)."""
        if not self.session.exists(place):
            raise ValueError(f"there is no component '{place}': see list_components")
        shape = {"ref": place, **(params or {}), "x": x, "y": y, "rotation": rotation}
        self.add_shape(component, name, shape)
        return f"placed {place} as {name}"

    def new_component(self, name: str, description: str = "") -> str:
        """A new, empty component, made the one edited."""
        made = self.session.components.new(name)
        if description:
            self.session.components.set_description(made, description)
        return f"created {made}"

    def set_component_yaml(self, component: str, text: str) -> str:
        """Replace a component's whole definition (a new one is created), as one edit."""
        project = self.session.project
        if component not in project.components and self.session.exists(component):
            raise ValueError(f"'{component}' is a library's: it is read-only")
        data = yaml_format.load(text) or {}
        definition, private = component_from_data(component, data)
        if private:
            raise ValueError("private components cannot be written here: make them top-level")
        existing = project.components.get(component)
        if existing is not None and not definition.category:
            definition.category = existing.category

        def change(p) -> None:
            p.components[component] = definition.model_copy(deep=True)

        new = existing is None
        self.session.edit(f"{'Create' if new else 'Replace'} {component}", change)
        self.session.set_active(component)
        return f"{'created' if new else 'replaced'} {component}"

    # -- building, checking, exporting ---------------------------------------

    def build(
        self, component: str | None = None, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """What a component builds to, with trial parameter values."""
        name = self._component(component)
        built = self._build(name, params)
        geometry = built.geometry
        layers = {}
        for layer in geometry.layer_names():
            region = geometry.layers[layer]
            layers[layer] = {"area_um2": _round(region.area() / 1e6), "polygons": region.count()}
        box = geometry.bbox()
        return {
            "component": name,
            "bbox_um": [_round(v) for v in box] if box else None,
            "layers": layers,
            "points": {n: [_round(x), _round(y)] for n, (x, y) in built.points().items()},
            "ports": {
                n: {
                    "at": [_round(p.x), _round(p.y)],
                    "angle": _round(p.angle),
                    "width": _round(p.width),
                    "layer": p.layer,
                }
                for n, p in built.ports().items()
            },
        }

    def check(
        self, component: str | None = None, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """The process's rule checks on a component, as an export would write it."""
        name = self._component(component)
        project = self.session.project
        geometry = rules.checked_geometry(project, name, params or {})
        violations = rules.check(project, geometry, name)
        return {
            "component": name,
            "errors": len(rules.errors(violations)),
            "warnings": len(rules.warnings(violations)),
            "violations": [_violation(v) for v in violations],
        }

    def violations(self, component: str | None, params: dict[str, Any] | None):
        name = self._component(component)
        project = self.session.project
        return rules.check(project, rules.checked_geometry(project, name, params or {}), name)

    def export(
        self,
        path: str,
        component: str | None = None,
        format: str | None = None,
        params: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> str:
        """Write a component to GDS, OASIS, DXF (or JSON, XML, MAT): by the file's
        extension unless ``format`` is given."""
        name = self._component(component)
        written, output = export_with_report(
            self.session.project,
            Path(path),
            format_name=format,
            component=name,
            params=params or {},
            options=options,
        )
        note = output.describe() if output is not None and hasattr(output, "describe") else ""
        return f"wrote {written}" + (f" ({note})" if note else "")

    # -- for rendering -------------------------------------------------------

    def built(self, component: str | None, params: dict[str, Any] | None) -> tuple[str, Build]:
        name = self._component(component)
        return name, self._build(name, params)

    def variants(self, sweep: dict[str, list[Any]] | list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Parameter sets: a list as given, or every combination of a sweep."""
        if isinstance(sweep, list):
            sets = [dict(s) for s in sweep]
        else:
            keys = list(sweep)
            sets = [dict(zip(keys, v, strict=True)) for v in itertools.product(*sweep.values())]
        if not sets:
            raise ValueError("give at least one set of parameter values")
        if len(sets) > MAX_VARIANTS:
            raise ValueError(f"{len(sets)} variants: at most {MAX_VARIANTS} in one image")
        return sets

    # -- helpers -------------------------------------------------------------

    def _component(self, component: str | None) -> str:
        name = component or self.session.active
        if not self.session.exists(name):
            known = ", ".join(sorted(self.session.project.components)) or "none"
            raise ValueError(f"there is no component '{name}' (the project's: {known})")
        return name

    def _edit(self, component: str | None) -> None:
        """Make the component to edit active; refuse a library's."""
        name = self._component(component)
        if name not in self.session.project.components:
            raise ValueError(f"'{name}' is a library's: it is read-only (copy it to edit it)")
        self.session.set_active(name)

    def _named(self) -> list[tuple[str, tuple[NodePath, Shape]]]:
        return [(s.name, (p, s)) for p, s in paths(self.session.shapes) if s.name]

    def _find(self, name: str) -> tuple[NodePath, Shape]:
        for found, at in self._named():
            if found == name:
                return at
        names = ", ".join(n for n, _ in self._named()) or "none"
        raise ValueError(f"there is no shape '{name}' in {self.session.active} (it has: {names})")

    def _build(self, name: str, params: dict[str, Any] | None) -> Build:
        return self.session.engine.build(name, params or {})


def _shape(name: str, data: dict[str, Any]) -> Shape:
    if not isinstance(data, dict):
        raise ValueError("a shape is a map of its fields, like {kind: rect, x1: 10, y1: 5}")  # noqa: TRY004
    [model] = shapes_from_data({name: data})
    try:
        return kind_class(model["kind"]).model_validate(model)
    except KeyError as error:
        raise ValueError(f"unknown kind '{model['kind']}': see describe_kinds") from error


def explain(error: Exception) -> str:
    """An error as the tool says it, as the editor would (one problem per field)."""
    return errors.explain(error)


def _entry(name: str, definition: ComponentDef, category: str = "") -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name}
    if definition.description:
        entry["description"] = definition.description.strip()
    if category or definition.category:
        entry["category"] = category or definition.category
    if definition.tags:
        entry["tags"] = list(definition.tags)
    public = [p.name for p in definition.parameters if not p.internal]
    if public:
        entry["parameters"] = public
    if definition.ports:
        entry["ports"] = [p.name for p in definition.ports]
    return entry


def _violation(v) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "rule": v.rule,
        "severity": "waived" if v.waived else v.severity,
        "layer": v.layer,
        "message": v.message,
    }
    if v.bbox_um:
        entry["bbox_um"] = [_round(x) for x in v.bbox_um]
    if v.waived:
        entry["waived"] = v.waived
    return entry


def _round(value: float) -> float:
    return round(float(value), 6) + 0.0


def _names(data: dict[str, Any]) -> str:
    return ", ".join(k for k in data if k != "project") or "none"
