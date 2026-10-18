"""Ports (#181): where wires attach to a component. A port is a point with the
direction a wire leaves it in, the wire width it expects and the layer it runs
on; placed, it moves, turns and mirrors with the component, an array's copies
have theirs by index, and it is a point of the placement (``p1.port.signal``)."""

import pytest

from mems_sketch import ComponentDef, PortDef, RectShape, RefShape
from mems_sketch.core.project import new_project
from mems_sketch.core.shapes import Align, Port
from mems_sketch.core.shapes.modifiers import ArrayModifier, MirrorModifier, PolarArrayModifier
from mems_sketch.editing import EditSession
from mems_sketch.engine import Engine
from mems_sketch.storage.component_format import component_data, component_from_data


def pad(**port) -> ComponentDef:
    """A 20 µm pad with a port on its right edge, pointing right."""
    fields = {
        "name": "signal",
        "at": "body.right",
        "direction": "right",
        "width": 4,
        "layer": "metal",
    }
    return ComponentDef(
        name="pad",
        shapes=[RectShape(name="body", layer="device", x0=-10, y0=-10, x1=10, y1=10)],
        ports=[PortDef(**{**fields, **port})],
    )


def project(*shapes, ports=()):
    p = new_project("t")
    p.components["pad"] = pad()
    p.components["top"].shapes = list(shapes)
    p.components["top"].ports = list(ports)
    return p


def approx(port: Port):
    return pytest.approx(tuple(port[:4]))


def test_a_port_is_where_its_component_says():
    built = Engine().load(project()).build("pad")
    assert built.ports() == {"signal": Port(10, 0, 0, 4, "metal")}
    assert built.points() == {}  # a port is not one of the component's own points


def test_directions_by_name_or_angle_and_defaults():
    p = project()
    p.components["pad"].ports += [
        PortDef(name="north", direction="up"),
        PortDef(name="tilted", at="center", x=1, direction="30 + 15"),
        PortDef(name="plain"),
    ]
    ports = Engine().load(p).build("pad").ports()
    assert ports["north"].angle == 90
    assert ports["tilted"][:3] == pytest.approx((1, 0, 45))
    assert ports["plain"] == Port(0, 0, 0, 0, "device")  # right, any width, on its level


def test_placed_ports_turn_and_mirror_with_the_component():
    p = project(
        RefShape(name="turned", component="pad", x=100, rotation=90),
        RefShape(name="mirrored", component="pad", y=50, mirror_x=True),
    )
    p.components["pad"].ports.append(PortDef(name="up", at="body.top", direction="up"))
    records = Engine().load(p).build("top").records()
    turned, mirrored = records[((0, 0),)].ports, records[((0, 1),)].ports
    assert tuple(turned["signal"][:4]) == pytest.approx((100, 10, 90, 4))
    assert turned["signal"].layer == "metal"
    assert tuple(mirrored["up"][:3]) == pytest.approx((0, 40, 270))  # flipped in y: now down


def test_array_copies_have_their_ports_by_index():
    p = project(
        RefShape(
            name="p1", component="pad", modifiers=[ArrayModifier(columns=2, rows=2, dx=50, dy=30)]
        )
    )
    ports = Engine().load(p).build("top").records()[((0, 0),)].ports
    assert set(ports) == {"signal", "signal[0]", "signal[1]", "signal[2]", "signal[3]"}
    assert ports["signal"] == ports["signal[0]"]  # the first copy is the node's own place
    assert tuple(ports["signal[1]"][:2]) == pytest.approx((60, 0))  # along the row first
    assert tuple(ports["signal[2]"][:2]) == pytest.approx((10, 30))


def test_polar_copies_turn_their_ports():
    p = project(RefShape(name="p1", component="pad", modifiers=[PolarArrayModifier(count=4)]))
    ports = Engine().load(p).build("top").records()[((0, 0),)].ports
    assert tuple(ports["signal[1]"][:3]) == pytest.approx((0, 10, 90))


def test_a_mirror_modifier_keeps_the_original_ports():
    p = project(RefShape(name="p1", component="pad", x=30, modifiers=[MirrorModifier(axis="x")]))
    ports = Engine().load(p).build("top").records()[((0, 0),)].ports
    assert set(ports) == {"signal"} and tuple(ports["signal"][:2]) == pytest.approx((40, 0))


def test_a_port_is_a_point_to_align_to_and_measure():
    p = project(
        RefShape(name="p1", component="pad", x=100, rotation=90),
        RectShape(
            name="wire",
            layer="metal",
            x0=0,
            y0=0,
            x1="p1.port.signal.width",
            y1="p1.port.signal.angle / 10",
            align=Align(point="bottom_left", to="p1.port.signal"),
        ),
    )
    assert Engine().load(p).build("top").records()[((0, 1),)].geometry.bbox() == pytest.approx(
        (100, 10, 104, 19)
    )


def test_a_placement_can_be_aligned_by_its_port():
    p = project(
        RectShape(name="base", layer="device", x0=0, y0=0, x1=40, y1=40),
        RefShape(name="p1", component="pad", align=Align(point="port.signal", to="base.left")),
    )
    ports = Engine().load(p).build("top").records()[((0, 1),)].ports
    assert tuple(ports["signal"][:2]) == pytest.approx((0, 20))


def test_a_port_at_a_placed_port_takes_its_direction_width_and_layer():
    p = project(
        RefShape(
            name="p1", component="pad", rotation=180, modifiers=[ArrayModifier(columns=3, dx=50)]
        ),
        ports=[
            PortDef(name="out", at="p1.port.signal[2]"),
            PortDef(name="wider", at="p1.port.signal", width=8),
        ],
    )
    ports = Engine().load(p).build("top").ports()
    assert ports["out"] == Port(pytest.approx(90), pytest.approx(0), pytest.approx(180), 4, "metal")
    assert ports["wider"].width == 8 and ports["wider"].angle == pytest.approx(180)


def test_what_a_port_cannot_be():
    with pytest.raises(ValueError, match="not a valid port name"):
        PortDef(name="no good")
    with pytest.raises(ValueError, match="not a point reference"):
        PortDef(name="a", at="body.port.signal.x")
    with pytest.raises(ValueError, match="port names must be unique"):
        ComponentDef(name="c", ports=[PortDef(name="a"), PortDef(name="a")])
    for broken, message in (
        (PortDef(name="a", width=-1), "width cannot be negative"),
        (PortDef(name="a", at="nothing.port.x"), "no shape 'nothing'"),
    ):
        p = project()
        p.components["pad"].ports = [broken]
        with pytest.raises(ValueError, match=message):
            Engine().load(p).build("pad").ports()


def test_ports_round_trip_through_the_file_format():
    data = component_data(pad(direction=45))
    assert data["ports"] == {
        "signal": {"at": "body.right", "direction": 45.0, "width": 4.0, "layer": "metal"}
    }
    assert component_from_data("pad", data)[0].ports == pad(direction=45).ports


def test_renaming_a_port_follows_its_uses():
    p = project(
        RefShape(name="p1", component="pad", modifiers=[ArrayModifier(columns=2, dx=50)]),
        RectShape(
            name="wire",
            layer="metal",
            x0=0,
            y0=0,
            x1="p1.port.signal.x",
            y1=1,
            align=Align(to="p1.port.signal[1]"),
        ),
        RefShape(name="p2", component="pad", align=Align(point="port.signal", to="p1.top")),
        ports=[PortDef(name="out", at="p1.port.signal")],
    )
    p.rename_port("pad", "signal", "drive")
    top = p.components["top"]
    wire, p2 = top.shapes[1], top.shapes[2]
    assert wire.x1 == "p1.port.drive.x" and wire.align.to == "p1.port.drive[1]"
    assert p2.align.point == "port.drive"
    assert top.ports[0].at == "p1.port.drive"
    assert Engine().load(p).build("top").ports()["out"].angle == 0


def test_editing_ports():
    doc = EditSession()
    doc.ports.add(name="out", direction="down", width=3)
    assert [p.name for p in doc.active_definition.ports] == ["out"]
    assert doc.results.declared_ports()["out"].angle == 270
    doc.ports.update("out", name="drive", layer="level")
    assert doc.active_definition.ports[0].name == "drive"
    doc.undo()
    assert doc.active_definition.ports[0].name == "out"
    doc.ports.remove("out")
    assert doc.active_definition.ports == []
