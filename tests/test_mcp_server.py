"""The MCP server (#193): an assistant's session over the protocol, and what each
tool does with good and bad input. Every failure is a tool error with the
editor's message, never a crash; edits roll back, undo and stay off libraries."""

import base64
import json

import anyio
import pytest

pytest.importorskip("mcp")
pytest.importorskip("PySide6")

from mcp.client.client import Client

from mems_sketch.core.project import Project
from mems_sketch.editing import EditSession
from mems_sketch.gui.pictures import application
from mems_sketch.mcp_server.server import create_server
from mems_sketch.mcp_server.tools import DesignTools
from mems_sketch.storage import load

PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def tools():
    project = Project(name="t", process_name="mems.surface")
    project.use_process("mems.surface")
    project.components["top"] = project.components.get("top") or _empty("top")
    return DesignTools(EditSession(project))


def _empty(name):
    from mems_sketch.core.user_component import ComponentDef

    return ComponentDef(name=name)


def rect(x1="w", y1=20, layer="device") -> dict:
    return {"kind": "rect", "layer": layer, "x0": 0, "y0": 0, "x1": x1, "y1": y1}


def test_a_session_over_the_protocol_makes_builds_renders_and_checks(tools, tmp_path):
    """What an assistant does: learn the kinds, make a component, build it, look
    at it, sweep a parameter, check it, export and save it; a bad edit is a tool
    error, and the session goes on."""
    application()
    calls: dict = {}

    async def session():
        async with Client(create_server(tools)) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert {"add_shape", "build", "render", "render_variants", "check"} <= names

            async def call(tool, **arguments):
                result = await client.call_tool(tool, arguments)
                calls[tool] = result
                return result

            index = (await call("describe_kinds")).content[0].text
            assert "`route`" in index and "`array`" in index
            about = (await call("describe_kind", kind="rect")).content[0].text
            assert "`x1`: number or expression, required" in about
            assert "sind" in (await call("reference", topic="expressions")).content[0].text
            await call("set_parameter", name="w", default=40, min=1)
            await call("add_shape", name="plate", shape=rect())
            await call(
                "place_component", name="anchor", place="mems.anchor", x="plate.right.x + 20"
            )
            bad = await call("add_shape", name="oops", shape={"kind": "rect", "x1": 3})
            assert bad.is_error and "x0: is required" in bad.content[0].text
            built = json.loads((await call("build", params={"w": 30})).content[0].text)
            assert built["layers"]["device"]["area_um2"] == pytest.approx(
                30 * 20 + 40 * 40
            )  # the anchor pad: 40 square
            image = (await call("render", points=True)).content[0]
            assert image.type == "image" and base64.b64decode(image.data).startswith(PNG)
            sweep = (await call("render_variants", sweep={"w": [10, 40, 80]})).content[0]
            assert base64.b64decode(sweep.data).startswith(PNG)
            checked = (await call("check", image=True)).content
            assert json.loads(checked[0].text)["errors"] == 0 and checked[1].type == "image"
            await call("export", path=str(tmp_path / "out.gds"))
            await call("save", folder=str(tmp_path / "project"))

    anyio.run(session)
    assert not any(r.is_error for n, r in calls.items() if n != "add_shape")
    assert (tmp_path / "out.gds").stat().st_size > 0
    saved = load(tmp_path / "project")
    assert [s.name for s in saved.components["top"].shapes] == ["plate", "anchor"]


def test_errors_are_tool_results_that_say_why(tools):
    async def session():
        async with Client(create_server(tools)) as client:
            results = [
                await client.call_tool("get_component", {"component": "nowhere"}),
                await client.call_tool("undo", {}),
                await client.call_tool("update_shape", {"name": "ghost", "fields": {"x0": 1}}),
                await client.call_tool("open_project", {"path": "/no/such/project"}),
            ]
            return [(r.is_error, r.content[0].text) for r in results]

    errors = anyio.run(session)
    assert all(failed for failed, _ in errors)
    assert "there is no component 'nowhere'" in errors[0][1]
    assert "nothing to undo" in errors[1][1]
    assert "there is no shape 'ghost'" in errors[2][1]


def test_an_edit_that_breaks_the_build_is_rolled_back(tools):
    tools.add_shape(None, "plate", rect(x1=10))
    with pytest.raises(ValueError, match="nope"):
        tools.update_shape(None, "plate", {"x1": "nope + 1"})
    assert tools.session.shapes[0].x1 == 10
    tools.update_shape(None, "plate", {"x1": 30, "corner_radius": 2})
    assert tools.undo() == "undid: Edit plate"
    assert tools.session.shapes[0].x1 == 10


def test_library_components_are_read_only(tools):
    assert "read-only" in tools.get_component("mems.anchor")
    with pytest.raises(ValueError, match="read-only"):
        tools.add_shape("mems.anchor", "x", rect(x1=1))
    with pytest.raises(ValueError, match="read-only"):
        tools.set_component_yaml("mems.anchor", "shapes: {}")


def test_yaml_round_trips_and_makes_new_components(tools):
    tools.add_shape(None, "plate", rect(x1=10))
    tools.add_modifier(None, "plate", "array", {"columns": 3, "dx": 15})
    text = tools.get_component("top")
    assert "plate:" in text and "array" in text
    tools.set_component_yaml(
        "pad", "parameters: {s: 20}\nshapes:\n  body: {kind: rect, x0: 0, y0: 0, x1: s, y1: s}\n"
    )
    assert tools.session.active == "pad"
    assert tools.build("pad")["bbox_um"] == [0, 0, 20, 20]
    tools.set_point("pad", "tip", at="body.right", x=1)
    assert tools.build("pad")["points"]["tip"] == [21, 10]
    tools.place_component("top", "p", "pad", {"s": 5}, x=100)
    assert tools.build("top")["layers"]["device"]["polygons"] == 4
    tools.rename_shape("pad", "body", "square")
    assert tools.build("pad")["points"]["tip"] == [21, 10]  # its point followed
    with pytest.raises(ValueError, match="unknown field"):
        tools.set_component_yaml("pad", "colour: red")


def test_deleting_and_listing(tools):
    tools.add_shape(None, "plate", rect(x1=10))
    tools.delete_shape(None, "plate")
    assert tools.session.shapes == []
    listed = tools.list_components()
    assert {"project", "mems", "std"} <= listed.keys()
    assert all("components" in entry for entry in listed.values())  # one shape for all
    anchor = next(c for c in listed["mems"]["components"] if c["name"] == "mems.anchor")
    assert anchor["category"] and anchor["description"]
    with pytest.raises(ValueError, match="no library 'nope'"):
        tools.list_components("nope")


def test_variants_are_combinations_and_limited(tools):
    assert tools.variants({"a": [1, 2], "b": [3]}) == [{"a": 1, "b": 3}, {"a": 2, "b": 3}]
    with pytest.raises(ValueError, match="at most"):
        tools.variants({"a": list(range(30))})


def test_the_cli_command_needs_no_extra_to_be_listed():
    from mems_sketch.cli import _parser

    args = _parser().parse_args(["mcp"])
    assert args.project is None and args.handler.__name__ == "_mcp"


def test_the_command_serves_over_stdio():
    """As an MCP client starts it: ``mems-sketch-cli mcp PROJECT`` in a process."""
    import sys
    from pathlib import Path

    from mcp.client.stdio import StdioServerParameters

    example = Path(__file__).parent.parent / "examples" / "resonator"
    command = ["-m", "mems_sketch.cli", "mcp", str(example)]
    params = StdioServerParameters(
        command=sys.executable, args=command, env={"QT_QPA_PLATFORM": "offscreen"}
    )

    async def session():
        async with Client(params) as client:
            info = json.loads((await client.call_tool("project_info", {})).content[0].text)
            picture = await client.call_tool("render", {})
            return info, picture

    info, picture = anyio.run(session)
    assert "suspension" in info["components"]
    assert not picture.is_error and picture.content[0].type == "image"


def test_every_kind_is_described_readably(tools):
    from mems_sketch.core.shapes import KINDS
    from mems_sketch.core.shapes.modifiers import MODIFIER_KINDS

    index = tools.describe_kinds()
    assert len(index) < 3000  # an index, not a dump
    for model in (*KINDS, *MODIFIER_KINDS):
        kind = model.kind_name()
        assert f"`{kind}`" in index
        text = tools.describe_kind(kind)
        assert "Fields:" in text and "(no description)" not in text, kind
        assert "typing." not in text and "mems_sketch." not in text.split("Fields:")[1], kind
    assert "- `style`: one of round, chamfer, default round" in tools.describe_kind("corners")
    with pytest.raises(ValueError, match="there is no kind 'blob'"):
        tools.describe_kind("blob")


def test_reference_topics(tools):
    expressions = tools.reference("expressions")
    assert "`sind`" in expressions and "radians" in expressions
    assert "min_exclusive" in tools.reference("components")
    assert "`top_right`" in tools.reference("points")
    assert "`device`" in tools.reference("layers")  # this project's stack
    with pytest.raises(ValueError, match="the topics are expressions"):
        tools.reference("everything")


def test_degree_functions_draw_beams_at_angles(tools):
    tools.new_component("beam", "A beam along an angle")
    tools.set_parameter("beam", "a", 30)
    shape = {
        "kind": "path",
        "layer": "device",
        "width": 2,
        "points": [[0, 0], ["50 * cosd(a)", "50 * sind(a)"]],
    }
    tools.add_shape("beam", "b", shape)
    port = tools.build("beam")
    assert tools.session.active == "beam"
    assert port["layers"]["device"]["area_um2"] == pytest.approx(100, abs=0.01)  # 50 × 2, slanted
    tools.set_parameter("beam", "a", 90)
    assert tools.build("beam")["bbox_um"][2] == pytest.approx(1)  # exactly vertical: cosd(90) is 0


def test_a_connection_error_says_where_its_ends_are(tools):
    yaml = """
shapes:
  b1: {kind: path, layer: device, width: 4, points: [[0, 10], [0, 60]]}
  b2: {kind: path, layer: device, width: 4, points: [[10, 0], [60, 0]]}
  hub: {kind: connection, ends: [b1.port.start, b2.port.start], inner_radius: 50}
"""
    with pytest.raises(ValueError, match=r"the ends: b1\.port\.start at \(0, 10\) facing 270°"):
        tools.set_component_yaml("j", yaml)


def test_variants_zoom_in_and_ports_are_arrows(tools):
    application()
    tools.set_parameter(None, "w", 40)
    tools.add_shape(
        None, "b", {"kind": "path", "layer": "device", "width": 2, "points": [[0, 0], ["w", 0]]}
    )

    async def session():
        async with Client(create_server(tools)) as client:
            whole = await client.call_tool("render_variants", {"sweep": {"w": [20, 40]}})
            zoomed = await client.call_tool(
                "render_variants", {"sweep": {"w": [20, 40]}, "box": [-2, -2, 2, 2]}
            )
            ports = await client.call_tool("render", {"points": True})
            return whole, zoomed, ports

    whole, zoomed, ports = anyio.run(session)
    assert not (whole.is_error or zoomed.is_error or ports.is_error)
    assert whole.content[0].data != zoomed.content[0].data
    assert ports.content[0].type == "image"


def test_save_never_replaces_another_project_unasked(tools, tmp_path):
    other = DesignTools()
    other.new_project(str(tmp_path / "other"), "other")
    other.add_shape(None, "keep", rect(x1=5))
    other.save()
    with pytest.raises(ValueError, match="already holds a project"):
        tools.save(str(tmp_path / "other"))
    assert [s.name for s in load(tmp_path / "other").components["top"].shapes] == ["keep"]
    assert "saved in" in tools.save(str(tmp_path / "other"), replace=True)
