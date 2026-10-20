"""The MCP server: the design tools for AI assistants, over stdio.

    mems-sketch-cli mcp [PROJECT]

One more frontend over an :class:`~mems_sketch.editing.EditSession`, next to
the GUI and the CLI: an assistant opens a project, reads and edits components
in the file format, builds them, looks at them, checks them and exports them.
Every failure is a tool error with the message the editor would show; the
server never stops on one. Needs the ``mcp`` extra (and ``gui`` for pictures).
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from mems_sketch.gui.pictures import Picture, render_png
from mems_sketch.mcp_server.tools import DesignTools, explain

INSTRUCTIONS = """\
mems-sketch: parametric MEMS layout. A project has components; each is a tree of
named shapes (rect, polygon, circle, arc, path, route, placed components, booleans,
...) whose numbers can be expressions over its parameters and other shapes' points.
Libraries (mems, std) provide components to place, read-only.

Before writing, read reference('components') (the format) and
reference('expressions') (sin/cos take radians, sind/cosd degrees). Then: open_project
(or new_project, new_component), list_components and describe_kinds to learn what
exists, describe_kind for one kind's fields, get_component to read a component (YAML,
the file format), then edit (add_shape, update_shape, place_component, set_parameter,
set_point, or set_component_yaml for a whole definition), build to see sizes, points
and ports, render to look at it (points=true shows ports as arrows), check for the
process's rules, and save. An edit that would break a project that built is refused
with the reason; undo goes back one edit.
"""


def create_server(tools: DesignTools | None = None) -> MCPServer:
    """A server with every tool, on ``tools`` (a new, empty project by default)."""
    tools = tools or DesignTools()
    server = MCPServer("mems-sketch", instructions=INSTRUCTIONS)

    def tool(fn: Callable) -> Callable:
        """Register ``fn`` as a tool: its errors become tool errors, dicts JSON."""

        @functools.wraps(fn)
        async def run(*args: Any, **kwargs: Any) -> Any:
            try:
                result = fn(*args, **kwargs)
            except ToolError:
                raise
            except Exception as error:
                raise ToolError(_message(error)) from error
            if isinstance(result, dict | list) and not _has_image(result):
                return json.dumps(result, indent=1, ensure_ascii=False)
            return result

        server.add_tool(run, structured_output=False)
        return fn

    # -- project ---------------------------------------------------------------

    @tool
    def open_project(path: str) -> str:
        """Open a project: its folder, its project.yaml, or a one-file document."""
        return tools.open_project(path)

    @tool
    def new_project(folder: str, name: str, library: bool = False) -> str:
        """Create a project (or a library: components only) in a new folder and open it."""
        return tools.new_project(folder, name, library)

    @tool
    def save(folder: str | None = None, replace: bool = False) -> str:
        """Save the project (in its folder, or in ``folder``). A folder that holds
        another project is refused unless ``replace`` is true, which overwrites it."""
        return tools.save(folder, replace)

    @tool
    def undo() -> str:
        """Undo the last edit."""
        return tools.undo()

    @tool
    def redo() -> str:
        """Redo the last undone edit."""
        return tools.redo()

    @tool
    def project_info() -> str:
        """The open project: name, folder, components, libraries, process and layers."""
        return tools.project_summary()

    @tool
    def list_components(library: str | None = None) -> str:
        """The project's components and each library's (or one library's), with their
        descriptions, categories, tags, public parameters and ports."""
        return tools.list_components(library)

    # -- reading ---------------------------------------------------------------

    @tool
    def get_component(component: str | None = None) -> str:
        """A component as YAML in the file format: parameters, points, ports and its
        named shapes. Library components too (``mems.comb_drive``)."""
        return tools.get_component(component)

    @tool
    def describe_kinds() -> str:
        """Every shape kind and modifier, one line each. Read one with describe_kind."""
        return tools.describe_kinds()

    @tool
    def describe_kind(kind: str) -> str:
        """One shape kind or modifier (``rect``, ``connection``, ``array``...): what it
        does, each field with its type and default, and an example in the file format."""
        return tools.describe_kind(kind)

    @tool
    def reference(topic: str) -> str:
        """Reference for writing components. Topics: ``expressions`` (operators,
        functions — sind/cosd take degrees, sin/cos radians — and the names you can
        use), ``components`` (the format: parameters, points, ports, shapes,
        placements), ``points`` (points, sizes and ports of shapes), ``layers``
        (layer names and this project's layer stack). Read these before writing."""
        return tools.reference(topic)

    # -- editing ---------------------------------------------------------------

    @tool
    def add_shape(name: str, shape: dict[str, Any], component: str | None = None) -> str:
        """Add a top-level shape to a component (default: the open one), written as in
        the file format: {kind: rect, layer: device, x0: 0, y0: 0, x1: 10, y1: 5}."""
        return tools.add_shape(component, name, shape)

    @tool
    def update_shape(name: str, fields: dict[str, Any], component: str | None = None) -> str:
        """Change fields of a named shape (anywhere in the tree), as in the file
        format; a field set to null goes back to its default."""
        return tools.update_shape(component, name, fields)

    @tool
    def rename_shape(name: str, new_name: str, component: str | None = None) -> str:
        """Rename a shape; expressions and references that use it follow."""
        return tools.rename_shape(component, name, new_name)

    @tool
    def delete_shape(name: str, component: str | None = None) -> str:
        """Delete a named shape."""
        return tools.delete_shape(component, name)

    @tool
    def add_modifier(
        shape: str, kind: str, fields: dict[str, Any] | None = None, component: str | None = None
    ) -> str:
        """Add a modifier (array, polar_array, mirror, corners, perforate, ...) to the end
        of a shape's stack; see describe_kinds for their fields."""
        return tools.add_modifier(component, shape, kind, fields)

    @tool
    def set_parameter(
        name: str,
        default: float | str,
        component: str | None = None,
        min: float | str | None = None,
        max: float | str | None = None,
        integer: bool | None = None,
        internal: bool | None = None,
        description: str | None = None,
    ) -> str:
        """Create or change a parameter: its default (a number or an expression) and
        optionally its limits, whether it is whole, internal, and its description."""
        given = {"min": min, "max": max, "integer": integer, "internal": internal}
        given["description"] = description
        limits = {k: v for k, v in given.items() if v is not None}
        return tools.set_parameter(component, name, default, **limits)

    @tool
    def set_point(
        name: str,
        at: str | None = None,
        x: float | str = 0.0,
        y: float | str = 0.0,
        description: str = "",
        component: str | None = None,
    ) -> str:
        """Create or change one of a component's own points: (x, y) from ``at``
        (``beam.right``, ``center``) or the origin."""
        return tools.set_point(component, name, at, x, y, description)

    @tool
    def place_component(
        name: str,
        place: str,
        params: dict[str, Any] | None = None,
        x: float | str = 0.0,
        y: float | str = 0.0,
        rotation: float | str = 0.0,
        component: str | None = None,
    ) -> str:
        """Place a component (``mems.comb_drive``, or one of the project's) as a shape
        called ``name``, with parameter values and a position."""
        return tools.place_component(component, name, place, params, x, y, rotation)

    @tool
    def new_component(name: str, description: str = "") -> str:
        """Create a new, empty component in the project, to add shapes to."""
        return tools.new_component(name, description)

    @tool
    def set_component_yaml(component: str, yaml: str) -> str:
        """Replace a component's whole definition with YAML in the file format (as
        get_component returns it), or create a new component, as one edit."""
        return tools.set_component_yaml(component, yaml)

    # -- building and looking ------------------------------------------------

    @tool
    def build(component: str | None = None, params: dict[str, Any] | None = None) -> str:
        """Build a component with trial parameter values (not saved): its bounding box,
        each layer's area and polygon count, its points and ports. A build error
        names the shape it came from."""
        return tools.build(component, params)

    @tool
    def render(
        component: str | None = None,
        params: dict[str, Any] | None = None,
        points: bool = False,
        box: list[float] | None = None,
        width: int = 800,
        height: int = 600,
    ) -> Image:
        """A picture (PNG) of a component, layers in their canvas colours, with a scale
        bar; ``points`` marks and labels its points, and its ports as arrows (the way
        a wire leaves, a bar as wide as the wire); ``box`` [x0, y0, x1, y1] in µm
        zooms in."""
        _, built = tools.built(component, params)
        picture = Picture(built.geometry)
        if points:
            picture.points, picture.ports = dict(built.points()), _ports(built)
        return Image(data=_png([picture], tools, width, height, box), format="png")

    @tool
    def render_variants(
        sweep: dict[str, list[Any]] | list[dict[str, Any]],
        component: str | None = None,
        columns: int | None = None,
        box: list[float] | None = None,
        width: int = 360,
        height: int = 280,
    ) -> Image:
        """Pictures of a component for several parameter values side by side, at one
        scale: a sweep {param: [values], ...} (every combination) or a list of
        parameter sets. ``box`` [x0, y0, x1, y1] in µm zooms every picture in on the
        same place (to compare a detail); it is widened to the pictures' shape, and
        geometry beyond a picture's frame is cut off there. A variant that does not build says why in
        its caption."""
        name = tools._component(component)
        pictures = []
        for values in tools.variants(sweep):
            caption = ", ".join(f"{k}={v}" for k, v in values.items())
            try:
                _, built = tools.built(name, values)
                pictures.append(Picture(built.geometry, caption=caption))
            except Exception as error:  # noqa: BLE001 - shown in its place
                from mems_sketch.core.component import Geometry

                pictures.append(Picture(Geometry(), caption=f"{caption}: {_message(error)}"))
        if all(p.geometry.bbox() is None for p in pictures):
            raise ValueError(pictures[0].caption)
        return Image(data=_png(pictures, tools, width, height, box, columns), format="png")

    @tool
    def check(
        component: str | None = None,
        params: dict[str, Any] | None = None,
        image: bool = False,
    ) -> Any:
        """Run the process's design-rule checks on a component as an export would write
        it: each violation's rule, severity, message and box in µm; with ``image``, a
        picture with the violations boxed in red too."""
        summary = tools.check(component, params)
        text = json.dumps(summary, indent=1, ensure_ascii=False)
        if not image:
            return text
        _, built = tools.built(component, params)
        marks = [(tuple(v["bbox_um"]), v["rule"]) for v in summary["violations"] if "bbox_um" in v]
        picture = Picture(built.geometry, marks=marks)
        return [text, Image(data=_png([picture], tools, 800, 600, None), format="png")]

    @tool
    def export(
        path: str,
        component: str | None = None,
        format: str | None = None,
        params: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> str:
        """Export a component to a file: GDS, OASIS or DXF (also JSON, XML, MAT), by its
        extension unless ``format`` is given."""
        return tools.export(path, component, format, params, options)

    return server


def run(project: str | None = None) -> None:
    """Serve over stdio, with ``project`` open if given."""
    tools = DesignTools()
    if project:
        tools.open_project(project)
    try:  # painting is done on this thread, with an offscreen application
        from mems_sketch.gui.pictures import application

        application()
    except ValueError:
        pass  # no PySide6: everything but pictures works
    create_server(tools).run("stdio")


def _png(pictures, tools: DesignTools, width, height, box, columns=None) -> bytes:
    process = tools.session.project.process
    view = tuple(box) if box else None
    if view is not None and len(view) != 4:
        raise ValueError("box is [x0, y0, x1, y1] in µm")
    return render_png(
        pictures, list(process.layers), process.stack_order(), width, height, view, columns
    )


def _ports(built) -> dict[str, tuple[float, float, float, float]]:
    return {f"port.{n}": (p.x, p.y, p.angle, p.width) for n, p in built.ports().items()}


def _has_image(result: Any) -> bool:
    return isinstance(result, list) and any(isinstance(item, Image) for item in result)


def _message(error: Exception) -> str:
    return explain(error)
