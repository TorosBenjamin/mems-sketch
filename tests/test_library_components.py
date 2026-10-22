"""The bundled libraries' components (#179, #180): each builds at its defaults and
at its limits, the documented points are where they say, and against the mems
library's own process the springs, anchors and devices pass every check while
the rest have only the warnings of a part that hangs on something else."""

import math
import re

import pytest

from mems_sketch import RectShape, RefShape
from mems_sketch.core.expressions import evaluate, names_in
from mems_sketch.core.project import Project
from mems_sketch.engine import Engine
from mems_sketch.libraries import bundled_library
from mems_sketch.process.rules import check, checked_geometry

STD = sorted(bundled_library("std").components)
MEMS = sorted(bundled_library("mems").components)
ALL = [f"std.{n}" for n in STD] + [f"mems.{n}" for n in MEMS]

# Parts that draw on a role only some levels have: built where it exists.
ON_PROCESS = {"mems.dimple_array": "polysilicon"}
# Whole on their own: anchored, released, nothing to warn about.
CLEAN = {
    "mems.anchor",
    "mems.clamped_beam",
    "mems.guided_beam",
    "mems.u_spring",
    "mems.folded_flexure",
    "mems.crab_leg",
    "mems.lateral_resonator",
    "mems.accelerometer",
}


def on(process: str = "surface") -> Project:
    project = Project(name="t", process_name=f"mems.{process}")
    project.use_process(f"mems.{process}")
    return project


def violations(project: Project, component: str, params=None):
    return check(project, checked_geometry(project, component, params or {}), component)


def definition(name: str):
    library, _, component = name.partition(".")
    return bundled_library(library).components[component]


def defaults(name: str) -> dict[str, float]:
    """The parameters' default values, resolved in the order they depend on each other."""
    values: dict[str, float] = {}
    pending = list(definition(name).parameters)
    while pending:
        progress = False
        for parameter in list(pending):
            if names_in(str(parameter.default)) - values.keys():
                continue  # it needs one not resolved yet
            values[parameter.name] = evaluate(parameter.default, values)
            pending.remove(parameter)
            progress = True
        assert progress, f"{name}: defaults that depend on each other"
    return values


def limit_cases():
    """(component, parameter, value): each public parameter at each of its limits
    (just inside an exclusive one)."""
    for name in ALL:
        values = defaults(name)
        for parameter in definition(name).parameters:
            if parameter.internal:
                continue
            low, high = parameter.limits(values)
            for limit, exclusive, inward in (
                (low, parameter.min_exclusive, 1),
                (high, parameter.max_exclusive, -1),
            ):
                if limit is None:
                    continue
                if parameter.integer:
                    value = math.ceil(limit) if inward > 0 else math.floor(limit)
                    if exclusive and value == limit:
                        value += inward
                else:
                    value = limit + inward * max(abs(limit), 1) * 0.01 if exclusive else limit
                yield pytest.param(
                    name, parameter.name, value, id=f"{name}-{parameter.name}={value:g}"
                )


@pytest.mark.parametrize("name", ALL)
def test_each_builds_at_its_defaults(name):
    project = on(ON_PROCESS.get(name, "surface"))
    geometry = Engine().load(project).build(name).geometry
    assert not geometry.is_empty()
    assert definition(name).description and definition(name).tags and definition(name).category


@pytest.mark.parametrize("name, parameter, value", list(limit_cases()))
def test_each_builds_at_its_limits(name, parameter, value):
    """It builds, or refuses because another of its own parameters' defaults is
    then out of range (a tiny pad with the default enclosure): never a part
    inside it refusing what the component let through."""
    project = on(ON_PROCESS.get(name, "surface"))
    try:
        geometry = Engine().load(project).build(name, {parameter: value}).geometry
    except ValueError as error:
        own = re.search(r"parameter '(\w+)' of '([\w.]+)'", str(error))
        assert own, error
        assert own[1] != parameter and own[2].split(".")[-1] == name.split(".")[-1], error
        return
    assert not geometry.is_empty()


@pytest.mark.parametrize("name", sorted(CLEAN))
def test_springs_anchors_and_devices_pass_every_check(name):
    assert [(v.rule, v.message) for v in violations(on(), name)] == []


@pytest.mark.parametrize("name", sorted(set(ALL) - CLEAN - set(ON_PROCESS)))
def test_the_other_parts_have_only_the_warnings_of_a_part(name):
    """No errors; the warnings are of a part on its own: it floats (it hangs on a
    mass or an anchor), or, for test structures and pads, the etch cannot free it."""
    found = violations(on(), name)
    assert not [v for v in found if v.severity == "error"], name
    expected = {"device_anchored"} | ({"device_release"} if name.startswith("std.") else set())
    assert {v.rule for v in found} <= expected, name


def test_dimples_under_a_plate_pass_the_polysilicon_rules():
    project = on("polysilicon")
    project.components["top"].shapes = [
        RectShape(layer="poly1", x0=-20, y0=-20, x1=160, y1=160),
        RefShape(name="dimples", component="mems.dimple_array"),
    ]
    assert not [v for v in check(project, component="top") if v.rule.startswith("dimple")]


def point(name: str, which: str, params=None) -> tuple[float, float]:
    return Engine().load(on()).build(name, params or {}).points()[which]


@pytest.mark.parametrize(
    "name, which, expected",
    [
        ("mems.guided_beam", "tip", (0, 130)),  # pad half 10 + length 120
        ("mems.folded_flexure", "attach", (0, -17)),  # below the pad (10), gap 4, bar 3
        ("mems.u_spring", "attach", (18, 10)),
        ("mems.crab_leg", "attach", (69, 120)),  # pad half 10 + thigh 60, less half a beam
        ("mems.parallel_plate", "moving", (0, -4)),
        ("mems.differential_comb", "shuttle", (0, 32)),  # -overlap/2 + length + spine/2
        ("mems.proof_mass", "attach_top", (0, 81.25)),  # 16 rows × 10 + 2.5, halved
        ("mems.proof_mass", "attach_right", (101.25, 0)),
        ("std.release_test", "root", (0, 53.5)),  # half of 5 × 20 + 2 + 5 × 1
    ],
)
def test_documented_points(name, which, expected):
    assert point(name, which) == pytest.approx(expected)


@pytest.mark.parametrize(
    "name, which, expected",
    [
        # (x, y, angle, width): off the outside of the fixed spine, as wide as it
        ("mems.comb_drive", "drive", (0, -40, 270, 10)),
        ("mems.comb_drive_with_stops", "drive", (0, -40, 270, 10)),  # its comb's
        ("mems.differential_comb", "upper", (0, 104, 90, 10)),  # the turned comb's: up
        ("mems.parallel_plate", "fixed", (2, 77, 90, 9)),  # above the anchors
        ("std.bond_pad", "signal", (50, 0, 0, 25)),
        ("mems.lateral_resonator", "drive", (-159.25, 0, 180, 10)),  # the left comb's
        ("mems.accelerometer", "lower_right", (72, -156.25, 270, 9)),
    ],
)
def test_documented_ports(name, which, expected):
    port = Engine().load(on()).build(name).ports()[which]
    assert tuple(port[:4]) == pytest.approx(expected)
    assert port.layer == "device"  # on the level they are placed on


def test_the_stiffness_follows_the_formula():
    """E t w³ / L³ for the guided beam, in N/m (GPa · µm⁴ / µm³ = kN/m, so × 1000)."""
    values = defaults("mems.guided_beam")
    assert values["stiffness"] == pytest.approx(160e3 * 2 * 2**3 / 120**3)
    assert defaults("mems.clamped_beam")["stiffness"] == pytest.approx(16 * 160e3 * 2 * 8 / 200**3)


def test_the_u_spring_is_half_as_stiff_as_a_guided_beam():
    """Two guided beams in series: half one's stiffness, as its description says."""
    u = defaults("mems.u_spring")
    guided = evaluate("youngs_modulus * 1000 * thickness * beam_width ** 3 / length ** 3", u)
    assert u["stiffness"] == pytest.approx(guided / 2)
    assert "half that of a guided beam" in definition("mems.u_spring").description


def box_of(polygons):
    xs = [x for hull, _ in polygons for x, _ in hull]
    ys = [y for hull, _ in polygons for _, y in hull]
    return min(xs), min(ys), max(xs), max(ys)


def smallest(name: str, parameter: str) -> float:
    """``parameter`` just above its (exclusive) minimum at the other defaults."""
    low, _ = next(p for p in definition(name).parameters if p.name == parameter).limits(
        defaults(name)
    )
    return low * 1.0001


def test_the_die_frame_label_stays_inside_the_smallest_frame():
    """#258: at its smallest the label area keeps clear of the lower corner marks
    and inside the frame, instead of running across the scribe lane."""
    width, height = smallest("std.die_frame", "width"), smallest("std.die_frame", "height")
    geometry = (
        Engine().load(on()).build("std.die_frame", {"width": width, "height": height}).geometry
    )
    left, bottom, right, top = box_of(geometry.polygons("metal"))
    inside_x, inside_y = width / 2 - 50 - 50, height / 2 - 50 - 50  # lane, inset
    # between the corner marks (200) and their insets, as far from either
    assert left == pytest.approx(-inside_x + 200 + 50)
    assert right <= inside_x - 200 - 50 + 1e-6
    assert -inside_y - 1e-6 <= bottom and top <= inside_y + 1e-6


def placed(name: str, params: dict):
    """Build ``name`` placed in top with ``params``: where its limits are checked."""
    project = on()
    project.components["top"].shapes = [RefShape(name="part", component=name, params=params)]
    return Engine().load(project).build("top").geometry


def test_the_proof_mass_attachment_zones_stay_on_the_mass():
    """#258: at their largest the zones reach across the shorter edge, not past it."""
    values = defaults("mems.proof_mass")
    attach = min(values["width"], values["height"])
    params = {"attach": attach, "attach_depth": attach / 2}
    geometry = Engine().load(on()).build("mems.proof_mass", params).geometry
    width, height = values["width"], values["height"]
    assert box_of(geometry.polygons("device")) == pytest.approx(
        (-width / 2, -height / 2, width / 2, height / 2)
    )
    placed("mems.proof_mass", params)
    with pytest.raises(ValueError, match="'attach'"):
        placed("mems.proof_mass", {"attach": attach + 1})


def test_round_holes_are_the_roundest_a_plate_takes():
    """#258: a corner radius beyond half the hole is refused, not silently clipped."""
    hole = defaults("mems.perforated_plate")["hole"]
    placed("mems.perforated_plate", {"corner_radius": hole / 2})
    with pytest.raises(ValueError, match="corner_radius"):
        placed("mems.perforated_plate", {"corner_radius": hole})


def test_the_accelerometer_banks_are_mirrored():
    """Moving along +x closes the small gaps on the left and the large ones on the
    right: each bank's fixed fingers (and their anchors) sit beside its moving
    ones, on the right in the left banks and on the left in the right ones, so
    the anchors are symmetric about the centre only if the banks are mirrored."""
    geometry = Engine().load(on()).build("mems.accelerometer").geometry
    xs = sorted(
        round(sum(x for x, _ in hull) / len(hull), 3) for hull, _ in geometry.polygons("anchor")
    )
    assert len(xs) == 4 * 8 + 2  # four banks of eight, and the two flexures'
    assert xs == pytest.approx(sorted(-x for x in xs))


def test_the_gallery_pages_are_up_to_date():
    """docs/user/library-<name>.md are what docs/gallery.py makes of the components."""
    import importlib.util
    from pathlib import Path

    from mems_sketch.libraries import bundled_names

    docs = Path(__file__).parent.parent / "docs"
    spec = importlib.util.spec_from_file_location("gallery", docs / "gallery.py")
    gallery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gallery)
    for name in bundled_names():
        page = docs / "user" / f"library-{name}.md"
        assert page.read_text(encoding="utf-8") == gallery.page(name), (
            f"{page} is out of date: run python docs/gallery.py"
        )
        for component in bundled_library(name).components:
            assert (docs / "images" / "gallery" / name / f"{component}.png").is_file()
