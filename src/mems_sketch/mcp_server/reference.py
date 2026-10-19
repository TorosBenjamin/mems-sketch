"""The reference an assistant reads before writing components: the expression
language, the component format, points and layers. What can be generated is:
the functions, the point and size names, the open project's layer stack.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mems_sketch.core.expressions import RESERVED_NAMES
from mems_sketch.core.shapes.points import BBOX_POINTS, MIN_SIZES, PORT_MEASURES, SIZES

if TYPE_CHECKING:
    from mems_sketch.core.project import Project

EXPRESSIONS = """\
# Expressions

Every number in a component (a shape's field, a parameter's default or limit, a
point, a port, a modifier's field) can be an expression, written as a string:
`x1: "2 * w + gap"`. Expressions are evaluated in the order they depend on each
other; a loop is an error.

**Operators:** `+ - * / // % **`, unary `+ -`, parentheses. Numbers like `3`,
`2.5`, `1e-3`.

**Functions:** {functions}.
- `sin`, `cos`, `tan` take **radians**; `sind`, `cosd`, `tand` take **degrees**
  (and are exact at multiples of 90°: `cosd(90)` is 0). Angles in this tool —
  rotations, port directions, arc angles — are in degrees, so `sind`/`cosd` are
  usually what you want: a beam along angle `a` ends at `[L * cosd(a), L * sind(a)]`.
- `min` and `max` take two or more values; `round(x)` or `round(x, digits)`.

**Constant:** `pi`.

**Names:**
- the component's parameters (`w`, `gap`);
- `process.<constant>`: the process's constants (`process.min_gap`);
- a shape's points and sizes: `beam.right.x`, `beam.top.y`, `beam.width`,
  `pad.port.signal.x`, `pad.port.signal.angle` (see the `points` topic);
- inside an array modifier, `i` and `j`: the copy's column and row;
- in a modifier's fields, `self`: the shape as it is just before that modifier
  (`self.width`).

Units: lengths are µm, angles degrees, areas µm².
"""

COMPONENTS = """\
# The component format

A component, as get_component returns it and set_component_yaml takes it (YAML):

```yaml
description: What it is (shown where it is placed)
tags: [spring, flexure]
level: device            # optional: the layer-stack level it is drawn on by default
parameters:
  w: 4                                     # just a default
  L: {{default: 60, min: 0, min_exclusive: true, description: "Beam length (µm)"}}
  n: {{default: 3, integer: true, min: 1}}
  pitch: {{default: "w + gap", internal: true}}   # derived: not settable where placed
points:
  tip: {{at: beam.right, x: 2}}             # (x, y) from a point, else from the origin
ports:
  out: {{at: beam.port.end, direction: 0, width: w, layer: level}}
shapes:
  beam: {{kind: rect, layer: device, x0: 0, y0: "-w / 2", x1: L, y1: "w / 2"}}
  anchor: {{ref: mems.anchor, size: 20, x: -10}}   # a placed component
```

**Parameters:** `default` (number or expression over other parameters and
`process.*`), `min`, `max` (numbers or expressions), `min_exclusive`,
`max_exclusive` (the limit itself is refused), `integer`, `internal` (used only
inside: not offered where the component is placed), `description`. A bare value
is the default.

**Points:** `at` (a point of one of its top-level shapes, `beam.right`, or of the
whole component, `center`), `x`, `y` (offsets, expressions), `description`.

**Ports** (where wires attach): like points, plus `direction` (`right`, `up`,
`left`, `down` or an angle in degrees counter-clockwise from +x; an
expression: `a1`), `width` (the wire width it expects; empty: any) and `layer`
(default `level`). A port `at` another port (`beam.port.end`, `comb.port.drive`)
takes its direction, width and layer unless given. Ports turn and mirror with
the placement.

**Shapes** are a map by name. A shape is `{{kind: ..., <fields>}}` (see
describe_kind for each kind's fields), or `{{ref: <component>, <parameter>:
<value>, ..., x, y, rotation, mirror_x, level, align}}` to place a component.
Shapes that hold shapes (`boolean`'s `a` and `b`, `transform`'s `children`)
hold maps too. Every shape may also have:
- `align: {{point: left, to: other.right, dx: 0, dy: 0}}`: moved so that its
  point lands on another shape's point;
- `modifiers: [{{kind: array, columns: 3, dx: 20}}, ...]`: a stack applied in
  order (describe_kind lists the modifier kinds);
- `enabled: false`: kept (its points still count) but not drawn.

Names (shapes, parameters, points, ports) are letters, digits and `_`, not
starting with a digit.
"""

POINTS = """\
# Points, sizes and ports

**Every shape** (and placed component) has the points of the box around its
geometry: {bbox}. In expressions: `<shape>.<point>.x`, `.y`
(`base.right.x - 5`); in `align` and `at`: `<shape>.<point>`.

**Sizes:** `<shape>.{sizes}` (of that box); `<shape>.min.{min_sizes}` and
`<shape>.min.<point>` are of its minimal box (the smallest rectangle around it at
any angle).

**Ports** of a placed component, a path or a route: `<shape>.port.<name>` is a
point (`pad.port.signal.x`), with `.{port_measures}` too. A path or route has
`start` and `end` at its ends, pointing out of it, as wide as it. An array's
copies have theirs by index: `comb.port.drive[2]` (counted along the rows).

**A placed component's own points:** the points it declares, by name
(`spring.end`); list them with get_component on the component placed.

**The component's whole box:** the same names without a shape (`center`), in a
point's or port's `at`.

build lists a component's declared points and ports with their coordinates.
"""

LAYERS = """\
# Layers

A shape's `layer` is either relative to the component's level of the layer stack
or a layer by name:
- `level`: the component's level (its main layer);
- `level+1`, `level-1`: that many levels above or below;
- `level.<role>`: a role of that level, such as `level.anchor`;
- anything else: a layer by name.

Drawing on `level` (and roles) keeps a component usable on any level: a placement
can put it on another one (`level: level+1` or a level by name).

This project's process, {process}, from the bottom level up:
{stack}
"""

TOPICS = {
    "expressions": "the expression language: operators, functions (degrees and radians), names",
    "components": "the component format: parameters, points, ports, shapes, placements",
    "points": "the points, sizes and ports expressions and alignments can use",
    "layers": "how a shape names its layer, and this project's layer stack",
}


def reference(topic: str, project: Project) -> str:
    """The reference ``topic`` (see :data:`TOPICS`)."""
    if topic == "expressions":
        functions = sorted(RESERVED_NAMES - {"pi"})
        return EXPRESSIONS.format(functions=", ".join(f"`{f}`" for f in functions))
    if topic == "components":
        return COMPONENTS.format()
    if topic == "points":
        return POINTS.format(
            bbox=", ".join(f"`{p}`" for p in BBOX_POINTS),
            sizes="`, `.".join(SIZES),
            min_sizes="`, `min.".join(MIN_SIZES),
            port_measures="`, `.".join(PORT_MEASURES),
        )
    if topic == "layers":
        process = project.process
        lines = []
        for k, level in enumerate(process.levels):
            roles = ", ".join(f"{role}: {name}" for role, name in level.roles.items())
            lines.append(f"- level {k}: `{level.layer}`" + (f" ({roles})" if roles else ""))
        layers = ", ".join(f"`{n}`" for n in process.stack_order())
        stack = "\n".join(lines) or "(no layer stack declared: the default one)"
        return LAYERS.format(process=project.process_name, stack=f"{stack}\n\nLayers: {layers}.")
    known = "; ".join(f"{name} ({about})" for name, about in TOPICS.items())
    raise ValueError(f"there is no topic '{topic}': the topics are {known}")
