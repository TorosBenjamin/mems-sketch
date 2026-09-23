# Shape-Kind Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adding a shape kind is one new module plus one line in `KINDS`; nothing else in `core` or `gui` switches on kinds.

**Architecture:** `core/shapes.py` becomes the package `core/shapes/`. Each kind is a pydantic model in `core/shapes/kinds/` that derives from `Node` and implements `render`, `moved`, `placement`, `summary`, `icon_name`, `default` / `wrap`. `registry.py` builds the discriminated `Shape` union from `KINDS`. The evaluator, tree helpers and GUI call the methods instead of matching on classes. Spec: `docs/superpowers/specs/2026-09-23-shape-kind-registry-design.md`.

**Tech Stack:** Python 3.11+, pydantic 2, klayout.db, PySide6 (GUI only), pytest, ruff.

**Commands:** run everything from the repo root.
- Tests: `uv run --extra dev python -m pytest -q -p no:cacheprovider`
- Lint: `uv run --extra dev ruff check . && uv run --extra dev ruff format --check .`

Baseline before starting: 201 tests pass, ruff clean. Leave the uncommitted `examples/resonator/components/top.yaml` edit (the user's) and `uv.lock` alone; stash the example edit while running tests (it breaks 4 example tests), and never commit either.

---

## File map

| File | Responsibility |
|---|---|
| `src/mems_sketch/core/shapes/__init__.py` | Module docstring (the old one) and re-exports; `__all__` |
| `…/shapes/base.py` | `Value`, `Point`, `INDEX_NAMES`, `check_point_reference`, `Repeat`, `Align`, `RenderContext`, `Node`, `Primitive`, `Operation` |
| `…/shapes/geometry.py` | `ARC_TOLERANCE_UM`, `MAX_ARC_SEGMENTS`, `segments`, `arc_points`, `boolean_op`, `to_ictrans`, `apply_transform` |
| `…/shapes/kinds/__init__.py` | `KINDS` tuple |
| `…/shapes/kinds/{rect,polygon,circle,arc,path,ref,transform,boolean,offset,fillet,layer_map}.py` | One kind each |
| `…/shapes/registry.py` | `Shape`, `SHAPE_ADAPTER`, `BY_KIND`, `PRIMITIVE_KINDS`, `WRAPPERS`, `kind_class`, `default_shape`, `wrap_shapes` |
| `…/shapes/tree.py` | `NodePath`, `child_lists`, `walk`, `references`, `container_of`, `node_at`, `paths`, `placement_of`, `find`, `visible_from` |
| `…/shapes/points.py` | `BBOX_POINTS`, `NodePoints`, `point_names`, `point_values`, `own_strings`, `point_dependencies` |
| `…/shapes/render.py` | `NodeRecord`, `Evaluator`, `transform_of`, `frame_of` |
| `…/shapes/rewrite.py` | `offset_value`, `translated`, `map_expressions`, `rewrite`, `rename_node_references` |
| `tests/test_shape_kinds.py` | Contract test over every registered kind |
| `tests/test_architecture.py` | + guard: only `kinds/` switches on kinds |
| `gui/panels.py`, `gui/icons.py`, `gui/document.py`, `gui/properties.py` | Use the kind methods |

---

### Task 1: Pure move into a package

No logic changes. Private helpers used across modules get public names:
`_boolean→boolean_op`, `_segments→segments`, `_arc_points→arc_points`,
`_annular_sector→annular_sector`, `_path→path_of`, `_apply→apply_transform`,
`_SHAPE_ADAPTER→SHAPE_ADAPTER`. In this task the models all live in `base.py`
(they move into `kinds/` in Task 2).

**Files:**
- Delete: `src/mems_sketch/core/shapes.py`
- Create: `src/mems_sketch/core/shapes/{__init__,base,geometry,tree,points,render,rewrite}.py`

- [ ] **Step 1: Split the file with a script**

Save as `$SCRATCH/split_shapes.py` and run `uv run python $SCRATCH/split_shapes.py` from the repo root. Line numbers refer to `shapes.py` at commit `f3ee80a`.

```python
import re
import subprocess
from pathlib import Path

src = subprocess.run(
    ["git", "show", "f3ee80a:src/mems_sketch/core/shapes.py"],
    capture_output=True,
    text=True,
    check=True,
).stdout.splitlines(keepends=True)


def lines(a, b):  # 1-based, inclusive
    return "".join(src[a - 1 : b])


RENAMES = {
    r"\b_boolean\b": "boolean_op",
    r"\b_segments\b": "segments",
    r"\b_arc_points\b": "arc_points",
    r"\b_annular_sector\b": "annular_sector",
    r"\b_path\b": "path_of",
    r"\b_apply\b": "apply_transform",
    r"\b_SHAPE_ADAPTER\b": "SHAPE_ADAPTER",
}


def renamed(text):
    for pattern, new in RENAMES.items():
        text = re.sub(pattern, new, text)
    return text


KINDS = (
    "ArcShape, BooleanShape, CircleShape, FilletShape, LayerMapShape, OffsetShape, "
    "PathShape, PolygonShape, RectShape, RefShape, TransformShape"
)
out = Path("src/mems_sketch/core/shapes")
out.mkdir()
Path("src/mems_sketch/core/shapes.py").unlink()

(out / "base.py").write_text(
    '"""Shape models: the nodes of the parametric shape tree."""\n\n'
    "from __future__ import annotations\n\n"
    "from typing import Annotated, Literal\n\n"
    "from pydantic import (\n    BaseModel,\n    ConfigDict,\n    Field,\n    TypeAdapter,\n"
    "    field_validator,\n    model_validator,\n)\n\n"
    + lines(56, 57)
    + lines(72, 72)
    + "\n\n"
    + lines(75, 284)
    + "\nSHAPE_ADAPTER: TypeAdapter = TypeAdapter(Shape)\n"
)
(out / "geometry.py").write_text(
    renamed(
        '"""Geometry helpers for the shape kinds: arcs, booleans, transforms."""\n\n'
        "from __future__ import annotations\n\n"
        "import math\nfrom typing import TYPE_CHECKING\n\n"
        "import klayout.db as kdb\n\n"
        "from mems_sketch.core.component import Geometry, to_dbu\n\n"
        "if TYPE_CHECKING:\n"
        "    from mems_sketch.core.shapes.base import ArcShape, PathShape, Point\n\n"
        + lines(58, 59)
        + "\n\n"
        + lines(749, 762)
        + "\n"
        + lines(923, 940)
        + "\n"
        + lines(953, 1005)
    )
)
(out / "tree.py").write_text(
    '"""Walking and addressing the shape tree."""\n\n'
    "from __future__ import annotations\n\n"
    "from collections.abc import Iterator\n\n"
    "import klayout.db as kdb\n\n"
    "from mems_sketch.core.component import to_dbu\n"
    "from mems_sketch.core.expressions import evaluate\n"
    "from mems_sketch.core.shapes.base import (\n    BooleanShape,\n    FilletShape,\n"
    "    LayerMapShape,\n    OffsetShape,\n    RefShape,\n    Shape,\n    TransformShape,\n)\n\n\n"
    + lines(290, 374)
    + "\n"
    + lines(1020, 1029)
)
(out / "points.py").write_text(
    '"""Alignment points of evaluated nodes, and the points expressions use."""\n\n'
    "from __future__ import annotations\n\n"
    "import functools\nfrom collections.abc import Iterator, Mapping\nfrom typing import Any\n\n"
    "import klayout.db as kdb\nfrom pydantic import BaseModel\n\n"
    "from mems_sketch.core.component import DBU_UM, Geometry\n"
    "from mems_sketch.core.expressions import ExpressionError, names_in\n"
    "from mems_sketch.core.shapes.base import Point, Shape\n"
    "from mems_sketch.core.shapes.tree import walk\n\n"
    "# Points every node has, from the bounding box of its geometry.\n"
    + lines(61, 71)
    + "\n\n"
    + lines(379, 500)
)
(out / "render.py").write_text(
    renamed(
        '"""Evaluating a shape tree into geometry."""\n\n'
        "from __future__ import annotations\n\n"
        "from collections.abc import Callable, Mapping\nfrom dataclasses import dataclass\n"
        "from typing import TYPE_CHECKING\n\n"
        "import klayout.db as kdb\n\n"
        "from mems_sketch.core.component import DBU_UM, Geometry, placement, resolve_params, to_dbu\n"
        "from mems_sketch.core.expressions import evaluate\n"
        f"from mems_sketch.core.shapes.base import (\n    {KINDS},\n    Point,\n    Repeat,\n    Shape,\n    Value,\n)\n"
        "from mems_sketch.core.shapes.geometry import (\n    ARC_TOLERANCE_UM,\n    annular_sector,\n"
        "    apply_transform,\n    arc_points,\n    boolean_op,\n    path_of,\n    segments,\n    to_ictrans,\n)\n"
        "from mems_sketch.core.shapes.points import NodePoints, own_strings, point_dependencies, point_values\n"
        "from mems_sketch.core.shapes.tree import NodePath\n\n"
        "if TYPE_CHECKING:\n    from mems_sketch.core.component import Component\n\n\n"
        + lines(505, 747)
        + "\n"
        + lines(942, 951)
        + "\n"
        + lines(1012, 1018)
    )
)
(out / "rewrite.py").write_text(
    renamed(
        '"""Rewriting shapes: moving them and changing the names their expressions use."""\n\n'
        "from __future__ import annotations\n\n"
        "import ast\nfrom collections.abc import Callable\nfrom typing import Any\n\n"
        "from mems_sketch.core.expressions import ExpressionError, substitute\n"
        f"from mems_sketch.core.shapes.base import (\n    {KINDS},\n    SHAPE_ADAPTER,\n    Shape,\n    Value,\n)\n"
        "from mems_sketch.core.shapes.points import point_names\n"
        "from mems_sketch.core.shapes.tree import walk\n\n\n" + lines(767, 921)
    )
)
(out / "__init__.py").write_text(
    lines(1, 37) + "from mems_sketch.core.shapes.base import (\n"
    "    INDEX_NAMES,\n    PRIMITIVE_KINDS,\n    SHAPE_ADAPTER,\n    Align,\n    ArcShape,\n"
    "    BooleanShape,\n    CircleShape,\n    FilletShape,\n    GroupShape,\n    LayerMapShape,\n"
    "    OffsetShape,\n    PathShape,\n    Point,\n    PolygonShape,\n    RectShape,\n    RefShape,\n"
    "    Repeat,\n    Shape,\n    TransformShape,\n    Value,\n    check_point_reference,\n)\n"
    "from mems_sketch.core.shapes.geometry import ARC_TOLERANCE_UM, MAX_ARC_SEGMENTS, to_ictrans\n"
    "from mems_sketch.core.shapes.points import (\n    BBOX_POINTS,\n    NodePoints,\n"
    "    own_strings,\n    point_dependencies,\n    point_names,\n    point_values,\n)\n"
    "from mems_sketch.core.shapes.render import Evaluator, NodeRecord, frame_of\n"
    "from mems_sketch.core.shapes.rewrite import (\n    map_expressions,\n    offset_value,\n"
    "    rename_node_references,\n    rewrite,\n    translated,\n)\n"
    "from mems_sketch.core.shapes.tree import (\n    NodePath,\n    child_lists,\n    container_of,\n"
    "    find,\n    node_at,\n    paths,\n    placement_of,\n    references,\n    visible_from,\n    walk,\n)\n"
)
```

- [ ] **Step 2: Add `__all__` and let ruff tidy imports**

Append to `src/mems_sketch/core/shapes/__init__.py` an `__all__` listing every name imported above (alphabetical), then run:

```bash
uv run --extra dev ruff check --fix src/mems_sketch/core/shapes && uv run --extra dev ruff format src/mems_sketch/core/shapes
uv run --extra dev ruff check .
```
Expected: `All checks passed!`. If ruff reports an unused import (F401) in a sub-module, delete that name from its import list; if it reports an undefined name (F821), add it to the import list from the module that now defines it (see the file map).

- [ ] **Step 3: Run the full suite**

Run the tests. Expected: `201 passed`.

- [ ] **Step 4: Commit**

```bash
git add -A src/mems_sketch/core/shapes src/mems_sketch/core/shapes.py
git commit -m "Split core/shapes.py into a package, unchanged (refactor 1/4)"
```

---

### Task 2: Node interface, kinds and registry

**Files:**
- Rewrite: `src/mems_sketch/core/shapes/base.py`, `geometry.py`
- Create: `src/mems_sketch/core/shapes/kinds/*.py`, `registry.py`
- Modify: `tree.py`, `points.py`, `render.py`, `rewrite.py`, `__init__.py`
- Test: `tests/test_shape_kinds.py`

- [ ] **Step 1: Write the contract test**

`tests/test_shape_kinds.py`:

```python
"""Every registered shape kind works with the rest of the system.

A new kind in core/shapes/kinds is checked here without adding tests: it must
survive a YAML round trip, render, move exactly, and describe itself.
"""

import klayout.db as kdb
import pytest
import yaml

from mems_sketch.core.component import get_component
from mems_sketch.core.shapes import (
    KINDS,
    SHAPE_ADAPTER,
    Evaluator,
    RectShape,
    RefShape,
    TransformShape,
    default_shape,
    kind_class,
    translated,
    wrap_shapes,
)

LAYER = "device"


def two_rects():
    return [
        RectShape(name="r1", layer=LAYER, x0=0, y0=0, x1=100, y1=50),
        RectShape(name="r2", layer=LAYER, x0=50, y0=10, x1=150, y1=60),
    ]


def samples():
    """One node of every kind: the primitives' defaults, every wrap, a reference."""
    shapes = [kind.default(LAYER) for kind in KINDS if kind.category == "primitive"]
    shapes += [wrap_shapes(op, f"w_{op}", two_rects()) for kind in KINDS for op in kind.wraps]
    shapes.append(RefShape(component="rectangle", x=5))
    return shapes


SAMPLES = samples()


def label(shape):
    return shape.name or shape.kind


def bbox(shape):
    box = kdb.Box()
    for region in Evaluator(get_component).render([shape], {}).layers.values():
        box += region.bbox()
    return box


def test_every_kind_has_a_sample():
    assert {type(s) for s in SAMPLES} == set(KINDS)


@pytest.mark.parametrize("kind", KINDS, ids=lambda k: k.kind_name())
def test_every_kind_declares_what_it_is(kind):
    assert kind.category in ("primitive", "operation", "reference")
    assert kind_class(kind.kind_name()) is kind
    assert kind.icon


@pytest.mark.parametrize("shape", SAMPLES, ids=label)
def test_a_yaml_round_trip_keeps_the_shape(shape):
    text = yaml.safe_dump(SHAPE_ADAPTER.dump_python(shape, mode="json"))
    assert SHAPE_ADAPTER.validate_python(yaml.safe_load(text)) == shape


@pytest.mark.parametrize("shape", SAMPLES, ids=label)
def test_the_shape_renders(shape):
    assert not bbox(shape).empty()


@pytest.mark.parametrize("shape", SAMPLES, ids=label)
def test_moving_shifts_the_geometry_exactly(shape):
    before, after = bbox(shape), bbox(translated(shape, 3, -2))
    assert (after.left - before.left, after.bottom - before.bottom) == (3000, -2000)
    assert (after.width(), after.height()) == (before.width(), before.height())


@pytest.mark.parametrize("shape", SAMPLES, ids=label)
def test_the_shape_describes_itself(shape):
    assert shape.summary()
    assert shape.icon_name()


def test_unknown_names_are_rejected():
    with pytest.raises(ValueError, match="unknown primitive"):
        default_shape("ref")
    with pytest.raises(ValueError, match="unknown operation"):
        wrap_shapes("explode", "x", two_rects())
    with pytest.raises(KeyError, match="unknown shape kind"):
        kind_class("nope")


def test_the_old_group_kind_is_read_as_a_transform():
    shape = SHAPE_ADAPTER.validate_python({"kind": "group", "children": []})
    assert isinstance(shape, TransformShape) and shape.kind == "transform"
    assert kind_class("group") is TransformShape
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run --extra dev python -m pytest -q -p no:cacheprovider tests/test_shape_kinds.py`
Expected: collection error, `ImportError: cannot import name 'KINDS'`.

- [ ] **Step 3: Rewrite `base.py`**

```python
"""What every shape kind is built on: the node base class, alignment and repeats.

A kind is a pydantic model deriving from :class:`Node` (usually through
:class:`Primitive` or :class:`Operation`) that renders itself, moves, lists
its children and describes itself for the GUI. The kinds are listed in
:mod:`mems_sketch.core.shapes.kinds`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

import klayout.db as kdb
from pydantic import BaseModel, ConfigDict, field_validator

from mems_sketch.core.expressions import evaluate

if TYPE_CHECKING:
    from mems_sketch.core.component import Component, Geometry
    from mems_sketch.core.shapes.points import NodePoints
    from mems_sketch.core.shapes.registry import Shape

Value = float | str  # a number or an expression
Point = tuple[float, float]
INDEX_NAMES = ("i", "j")
```

Then, unchanged from the old module: `check_point_reference`, `Repeat`, `Align`. Then:

```python
@dataclass(frozen=True)
class RenderContext:
    """What a kind needs to render one copy of itself."""

    variables: dict[str, float]  # parameters, point coordinates and the repeat indices
    scope: Mapping[str, NodePoints]  # the named nodes it can see
    lookup: Callable[[str], Component]  # components by name, for references
    render_lists: Callable[[list[list[Shape]], Mapping[str, NodePoints]], list[Geometry]]

    def ev(self, value: Value) -> float:
        return evaluate(value, self.variables)

    def children(
        self, lists: list[list[Shape]], scope: Mapping[str, NodePoints] | None = None
    ) -> list[Geometry]:
        """Geometry of each child list; ``scope`` replaces the visible points if given."""
        return self.render_lists(lists, self.scope if scope is None else scope)


class Node(BaseModel):
    """The fields every node has, and the methods a kind overrides."""

    model_config = ConfigDict(extra="forbid")

    category: ClassVar[str]  # "primitive", "operation" or "reference"
    icon: ClassVar[str] = "point"  # its icon in the GUI
    child_fields: ClassVar[tuple[str, ...]] = ()  # fields holding lists of child nodes
    placed: ClassVar[bool] = False  # has x, y, rotation and mirroring; see placement()
    wraps: ClassVar[tuple[str, ...]] = ()  # operations that create it around shapes

    name: str | None = None  # stable handle for the GUI and scripts
    align: Align | None = None
    enabled: bool = True
    repeat: Repeat | None = None

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        """Geometry of one copy, and the points it declares (only references declare any)."""
        raise NotImplementedError

    def child_lists(self) -> list[list[Shape]]:
        return [getattr(self, field) for field in self.child_fields]

    def moved(
        self,
        x: Callable[[Value], Value],
        y: Callable[[Value], Value],
        inner: Callable[[list[Shape]], list[Shape]],
    ) -> dict[str, Any]:
        """Fields changed by a move: ``x`` and ``y`` move one coordinate, ``inner`` a child list."""
        return {field: inner(getattr(self, field)) for field in self.child_fields}

    def placement(self, variables: dict[str, float]) -> kdb.DCplxTrans | None:
        """The transform a ``placed`` kind applies to its content, in µm."""
        return None

    def summary(self) -> str:
        """Short description next to its name in the shape tree."""
        return self.kind

    def icon_name(self) -> str:
        return self.icon

    @classmethod
    def kind_name(cls) -> str:
        return cls.model_fields["kind"].default

    @classmethod
    def default(cls, layer: str) -> Shape:
        """A new shape of this kind to start editing from (primitives)."""
        raise TypeError(f"'{cls.kind_name()}' has no default shape")

    @classmethod
    def wrap(cls, op: str, name: str, nodes: list[Shape]) -> Shape:
        """A new node of this kind holding ``nodes`` (kinds that list ``op`` in ``wraps``)."""
        raise TypeError(f"'{cls.kind_name()}' cannot wrap shapes")


class Primitive(Node):
    """A leaf drawn on one layer (subclasses declare ``layer``)."""

    category: ClassVar[str] = "primitive"

    def summary(self) -> str:
        return f"{self.kind} · {self.layer}"


class Operation(Node):
    """An inner node made from its children's geometry."""

    category: ClassVar[str] = "operation"
    child_fields: ClassVar[tuple[str, ...]] = ("children",)
```

- [ ] **Step 4: Trim `geometry.py`**

Delete `annular_sector` and `path_of` (they move into `kinds/arc.py` and `kinds/path.py`) and the `TYPE_CHECKING` block; keep `ARC_TOLERANCE_UM`, `MAX_ARC_SEGMENTS`, `to_ictrans`, `apply_transform`, `boolean_op`, `segments`, `arc_points`. Import `Point` from `base` under `TYPE_CHECKING` for `apply_transform`'s annotation.

- [ ] **Step 5: Write the kinds**

`kinds/rect.py`:

```python
"""Rectangle between two corners."""

from __future__ import annotations

from typing import ClassVar, Literal

from mems_sketch.core.component import Geometry
from mems_sketch.core.shapes.base import Point, Primitive, RenderContext, Value


class RectShape(Primitive):
    icon: ClassVar[str] = "rect"

    kind: Literal["rect"] = "rect"
    layer: str
    x0: Value
    y0: Value
    x1: Value
    y1: Value

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        geometry = Geometry()
        geometry.add_rect(
            self.layer, ctx.ev(self.x0), ctx.ev(self.y0), ctx.ev(self.x1), ctx.ev(self.y1)
        )
        return geometry, {}

    def moved(self, x, y, inner) -> dict:
        return {"x0": x(self.x0), "x1": x(self.x1), "y0": y(self.y0), "y1": y(self.y1)}

    @classmethod
    def default(cls, layer: str) -> RectShape:
        return cls(layer=layer, x0=0, y0=0, x1=100, y1=50)
```

`kinds/polygon.py`:

```python
"""Closed polygon through its points."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field

from mems_sketch.core.component import Geometry
from mems_sketch.core.shapes.base import Point, Primitive, RenderContext, Value


class PolygonShape(Primitive):
    icon: ClassVar[str] = "polygon"

    kind: Literal["polygon"] = "polygon"
    layer: str
    points: list[tuple[Value, Value]] = Field(min_length=3)

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        geometry = Geometry()
        geometry.add_polygon(self.layer, [(ctx.ev(x), ctx.ev(y)) for x, y in self.points])
        return geometry, {}

    def moved(self, x, y, inner) -> dict:
        return {"points": [(x(px), y(py)) for px, py in self.points]}

    @classmethod
    def default(cls, layer: str) -> PolygonShape:
        return cls(layer=layer, points=[(0, 0), (60, 0), (30, 50)])
```

`kinds/circle.py`:

```python
"""Circle, approximated by segments."""

from __future__ import annotations

from typing import ClassVar, Literal

from mems_sketch.core.component import Geometry
from mems_sketch.core.shapes.base import Point, Primitive, RenderContext, Value
from mems_sketch.core.shapes.geometry import arc_points, segments


class CircleShape(Primitive):
    icon: ClassVar[str] = "circle"

    kind: Literal["circle"] = "circle"
    layer: str
    x: Value = 0.0
    y: Value = 0.0
    radius: Value
    segments: Value | None = None  # default: from ARC_TOLERANCE_UM

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        r = ctx.ev(self.radius)
        n = segments(r, self.segments and ctx.ev(self.segments))
        geometry = Geometry()
        geometry.add_polygon(self.layer, arc_points(ctx.ev(self.x), ctx.ev(self.y), r, 0, 360, n))
        return geometry, {}

    def moved(self, x, y, inner) -> dict:
        return {"x": x(self.x), "y": y(self.y)}

    @classmethod
    def default(cls, layer: str) -> CircleShape:
        return cls(layer=layer, radius=25)
```

`kinds/arc.py`:

```python
"""Annular sector: a ring, a disc, or part of one."""

from __future__ import annotations

from typing import ClassVar, Literal

import klayout.db as kdb

from mems_sketch.core.component import Geometry, to_dbu
from mems_sketch.core.shapes.base import Point, Primitive, RenderContext, Value
from mems_sketch.core.shapes.geometry import arc_points, segments


class ArcShape(Primitive):
    """Annular sector (a ring when the angles span 360°). Angles in degrees, CCW from +x."""

    icon: ClassVar[str] = "arc"

    kind: Literal["arc"] = "arc"
    layer: str
    x: Value = 0.0
    y: Value = 0.0
    inner_radius: Value = 0.0
    outer_radius: Value
    start_angle: Value = 0.0
    end_angle: Value = 360.0
    segments: Value | None = None  # for a full circle; scaled by the swept angle

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        geometry = Geometry()
        geometry.layers[self.layer] = self._sector(ctx)
        return geometry, {}

    def _sector(self, ctx: RenderContext) -> kdb.Region:
        """Outer pie minus inner pie; with full angles this is a ring or disc."""
        cx, cy = ctx.ev(self.x), ctx.ev(self.y)
        r_in, r_out = ctx.ev(self.inner_radius), ctx.ev(self.outer_radius)
        start, end = ctx.ev(self.start_angle), ctx.ev(self.end_angle)
        if not 0 <= r_in < r_out:
            raise ValueError("arc needs 0 <= inner_radius < outer_radius")
        if end <= start:
            raise ValueError("arc end_angle must be greater than start_angle")
        n = segments(r_out, self.segments and ctx.ev(self.segments))

        def pie(r: float) -> kdb.Region:
            points = arc_points(cx, cy, r, start, end, n)
            if end - start < 360:
                points.append((cx, cy))
            return kdb.Region(kdb.Polygon([kdb.Point(to_dbu(x), to_dbu(y)) for x, y in points]))

        return pie(r_out) - pie(r_in) if r_in > 0 else pie(r_out)

    def moved(self, x, y, inner) -> dict:
        return {"x": x(self.x), "y": y(self.y)}

    @classmethod
    def default(cls, layer: str) -> ArcShape:
        return cls(layer=layer, inner_radius=20, outer_radius=30, end_angle=180)
```

`kinds/path.py`:

```python
"""Wire of constant width along a centreline."""

from __future__ import annotations

from typing import ClassVar, Literal

import klayout.db as kdb
from pydantic import Field

from mems_sketch.core.component import Geometry, to_dbu
from mems_sketch.core.shapes.base import Point, Primitive, RenderContext, Value


class PathShape(Primitive):
    """A wire of constant ``width`` along a centreline, e.g. a beam or a trace."""

    icon: ClassVar[str] = "path"

    kind: Literal["path"] = "path"
    layer: str
    points: list[tuple[Value, Value]] = Field(min_length=2)
    width: Value
    ends: Literal["flush", "square", "round"] = "flush"

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        points = [kdb.Point(to_dbu(ctx.ev(x)), to_dbu(ctx.ev(y))) for x, y in self.points]
        width = to_dbu(ctx.ev(self.width))
        if width <= 0:
            raise ValueError("path width must be positive")
        ext = 0 if self.ends == "flush" else width // 2
        geometry = Geometry()
        geometry.region(self.layer).insert(kdb.Path(points, width, ext, ext, self.ends == "round"))
        return geometry, {}

    def moved(self, x, y, inner) -> dict:
        return {"points": [(x(px), y(py)) for px, py in self.points]}

    @classmethod
    def default(cls, layer: str) -> PathShape:
        return cls(layer=layer, points=[(0, 0), (100, 0), (100, 60)], width=4)
```

`kinds/ref.py`:

```python
"""Instance of another component."""

from __future__ import annotations

from typing import ClassVar, Literal

import klayout.db as kdb
from pydantic import Field

from mems_sketch.core.component import Geometry, resolve_params
from mems_sketch.core.expressions import evaluate
from mems_sketch.core.shapes.base import Node, Point, RenderContext, Value
from mems_sketch.core.shapes.geometry import apply_transform, to_ictrans


class RefShape(Node):
    """An instance of a built-in or user-defined component."""

    category: ClassVar[str] = "reference"
    icon: ClassVar[str] = "component"
    placed: ClassVar[bool] = True

    kind: Literal["ref"] = "ref"
    component: str
    params: dict[str, Value] = Field(default_factory=dict)
    x: Value = 0.0
    y: Value = 0.0
    rotation: Value = 0.0  # degrees, counter-clockwise
    mirror_x: bool = False

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        child = ctx.lookup(self.component)
        built, points = child.compile(resolve_params(child, self.params, ctx.variables))
        transform = self.placement(ctx.variables)
        geometry = Geometry()
        geometry.merge(built, to_ictrans(transform))
        return geometry, {name: apply_transform(transform, p) for name, p in points.items()}

    def moved(self, x, y, inner) -> dict:
        return {"x": x(self.x), "y": y(self.y)}

    def placement(self, variables: dict[str, float]) -> kdb.DCplxTrans:
        return kdb.DCplxTrans(
            1.0,
            evaluate(self.rotation, variables),
            self.mirror_x,
            evaluate(self.x, variables),
            evaluate(self.y, variables),
        )

    def summary(self) -> str:
        return self.component
```

`kinds/transform.py`:

```python
"""Children moved, rotated, mirrored and scaled as one piece."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

import klayout.db as kdb
from pydantic import Field, field_validator

from mems_sketch.core.component import Geometry
from mems_sketch.core.expressions import evaluate
from mems_sketch.core.shapes.base import Operation, Point, RenderContext, Value
from mems_sketch.core.shapes.geometry import to_ictrans

if TYPE_CHECKING:
    from mems_sketch.core.shapes.registry import Shape


class TransformShape(Operation):
    """Mirror ``children`` about x, scale, rotate and move them as one piece.

    For something reusable, make a component instead; a transform is for
    moving a few shapes together once. Files written before the rename used
    ``kind: group``, which is still read.
    """

    icon: ClassVar[str] = "transform"
    placed: ClassVar[bool] = True
    wraps: ClassVar[tuple[str, ...]] = ("transform", "group")

    kind: Literal["transform", "group"] = "transform"
    children: list[Shape] = Field(default_factory=list)
    x: Value = 0.0
    y: Value = 0.0
    rotation: Value = 0.0
    mirror_x: bool = False
    scale: Value = 1.0

    @field_validator("kind")
    @classmethod
    def _current_kind(cls, kind: str) -> str:
        return "transform"

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        if ctx.ev(self.scale) <= 0:
            raise ValueError("transform scale must be positive")
        transform = self.placement(ctx.variables)
        inverse = transform.inverted()
        inner_scope = {k: p.seen_through(inverse) for k, p in ctx.scope.items()}
        (inner,) = ctx.children([self.children], inner_scope)
        geometry = Geometry()
        geometry.merge(inner, to_ictrans(transform))
        return geometry, {}

    def moved(self, x, y, inner) -> dict:
        return {"x": x(self.x), "y": y(self.y)}  # the children stay in their own frame

    def placement(self, variables: dict[str, float]) -> kdb.DCplxTrans:
        return kdb.DCplxTrans(
            evaluate(self.scale, variables),
            evaluate(self.rotation, variables),
            self.mirror_x,
            evaluate(self.x, variables),
            evaluate(self.y, variables),
        )

    @classmethod
    def wrap(cls, op: str, name: str, nodes: list[Shape]) -> TransformShape:
        return cls(name=name, children=nodes)
```

`kinds/boolean.py`:

```python
"""Boolean of two child lists, per layer."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

from mems_sketch.core.component import Geometry
from mems_sketch.core.shapes.base import Operation, Point, RenderContext
from mems_sketch.core.shapes.geometry import boolean_op

if TYPE_CHECKING:
    from mems_sketch.core.shapes.registry import Shape


class BooleanShape(Operation):
    icon: ClassVar[str] = "union"
    child_fields: ClassVar[tuple[str, ...]] = ("a", "b")
    wraps: ClassVar[tuple[str, ...]] = ("union", "subtract", "intersect", "xor")

    kind: Literal["boolean"] = "boolean"
    op: Literal["union", "subtract", "intersect", "xor"]
    a: list[Shape]
    b: list[Shape]

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        a, b = ctx.children([self.a, self.b])
        return boolean_op(self.op, a, b), {}

    def summary(self) -> str:
        return self.op

    def icon_name(self) -> str:
        return self.op

    @classmethod
    def wrap(cls, op: str, name: str, nodes: list[Shape]) -> BooleanShape:
        if len(nodes) < 2:
            raise ValueError(f"{op} needs at least two selected shapes")
        return cls(name=name, op=op, a=nodes[:1], b=nodes[1:])
```

`kinds/offset.py`:

```python
"""Children's outlines grown or shrunk."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

from mems_sketch.core.component import Geometry, to_dbu
from mems_sketch.core.shapes.base import Operation, Point, RenderContext, Value

if TYPE_CHECKING:
    from mems_sketch.core.shapes.registry import Shape


class OffsetShape(Operation):
    """Grow (positive ``distance``) or shrink (negative) the children's outlines."""

    icon: ClassVar[str] = "offset"
    wraps: ClassVar[tuple[str, ...]] = ("offset",)

    kind: Literal["offset"] = "offset"
    children: list[Shape]
    distance: Value
    corners: Literal["square", "bevel"] = "square"

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        mode = 2 if self.corners == "square" else 1
        d = to_dbu(ctx.ev(self.distance))
        geometry = Geometry()
        for layer, region in ctx.children([self.children])[0].layers.items():
            geometry.layers[layer] = region.sized(d, mode)
        return geometry, {}

    def summary(self) -> str:
        return f"offset {self.distance}"

    @classmethod
    def wrap(cls, op: str, name: str, nodes: list[Shape]) -> OffsetShape:
        return cls(name=name, distance=1.0, children=nodes)
```

`kinds/fillet.py`:

```python
"""Children's corners rounded."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

from mems_sketch.core.component import Geometry, to_dbu
from mems_sketch.core.shapes.base import Operation, Point, RenderContext, Value
from mems_sketch.core.shapes.geometry import ARC_TOLERANCE_UM, segments

if TYPE_CHECKING:
    from mems_sketch.core.shapes.registry import Shape


class FilletShape(Operation):
    """Round corners: ``radius`` for convex corners, ``inner_radius`` for concave ones."""

    icon: ClassVar[str] = "fillet"
    wraps: ClassVar[tuple[str, ...]] = ("fillet",)

    kind: Literal["fillet"] = "fillet"
    children: list[Shape]
    radius: Value = 0.0
    inner_radius: Value = 0.0
    segments: Value | None = None  # per full circle; default from ARC_TOLERANCE_UM

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        r_out, r_in = ctx.ev(self.radius), ctx.ev(self.inner_radius)
        if r_out < 0 or r_in < 0:
            raise ValueError("fillet radii must not be negative")
        n = segments(max(r_out, r_in, ARC_TOLERANCE_UM), self.segments and ctx.ev(self.segments))
        geometry = Geometry()
        for layer, region in ctx.children([self.children])[0].layers.items():
            geometry.layers[layer] = region.merged().rounded_corners(to_dbu(r_in), to_dbu(r_out), n)
        return geometry, {}

    def summary(self) -> str:
        return f"fillet {self.radius}"

    @classmethod
    def wrap(cls, op: str, name: str, nodes: list[Shape]) -> FilletShape:
        return cls(name=name, radius=1.0, children=nodes)
```

`kinds/layer_map.py`:

```python
"""Children's geometry moved between layers."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

from pydantic import model_validator

from mems_sketch.core.component import Geometry
from mems_sketch.core.shapes.base import Operation, Point, RenderContext

if TYPE_CHECKING:
    from mems_sketch.core.shapes.registry import Shape


class LayerMapShape(Operation):
    """Move children's geometry between layers.

    ``mapping`` sends source layer -> target layer; several sources may merge
    into one target. Layers not in ``mapping`` are dropped unless
    ``keep_unmapped`` is set.
    """

    icon: ClassVar[str] = "layer_map"
    wraps: ClassVar[tuple[str, ...]] = ("layer_map",)

    kind: Literal["layer_map"] = "layer_map"
    children: list[Shape]
    mapping: dict[str, str]
    keep_unmapped: bool = False

    @model_validator(mode="after")
    def _not_empty(self):
        if not self.mapping:
            raise ValueError("layer_map needs at least one mapping")
        return self

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        geometry = Geometry()
        for layer, region in ctx.children([self.children])[0].layers.items():
            target = self.mapping.get(layer, layer if self.keep_unmapped else None)
            if target is not None:
                geometry.region(target).insert(region)
        return geometry, {}

    def summary(self) -> str:
        return "layers " + ", ".join(f"{a}→{b}" for a, b in self.mapping.items())

    @classmethod
    def wrap(cls, op: str, name: str, nodes: list[Shape]) -> LayerMapShape:
        layers = sorted(_layers(nodes))
        return cls(
            name=name,
            mapping={layer: layer for layer in layers} or {"device": "device"},
            children=nodes,
        )


def _layers(nodes: list[Shape]) -> set[str]:
    found = set()
    for node in nodes:
        if (layer := getattr(node, "layer", None)) is not None:
            found.add(layer)
        for children in node.child_lists():
            found |= _layers(children)
    return found
```

`kinds/__init__.py`:

```python
"""Every shape kind, in the order of the shape union.

To add a kind, write a module like ``rect.py`` (a :class:`~mems_sketch.core.shapes.base.Node`
subclass) and list its class in ``KINDS``. The contract test in
``tests/test_shape_kinds.py`` then checks it.
"""

from mems_sketch.core.shapes.kinds.arc import ArcShape
from mems_sketch.core.shapes.kinds.boolean import BooleanShape
from mems_sketch.core.shapes.kinds.circle import CircleShape
from mems_sketch.core.shapes.kinds.fillet import FilletShape
from mems_sketch.core.shapes.kinds.layer_map import LayerMapShape
from mems_sketch.core.shapes.kinds.offset import OffsetShape
from mems_sketch.core.shapes.kinds.path import PathShape
from mems_sketch.core.shapes.kinds.polygon import PolygonShape
from mems_sketch.core.shapes.kinds.rect import RectShape
from mems_sketch.core.shapes.kinds.ref import RefShape
from mems_sketch.core.shapes.kinds.transform import TransformShape

KINDS = (
    RectShape,
    PolygonShape,
    CircleShape,
    ArcShape,
    PathShape,
    RefShape,
    TransformShape,
    BooleanShape,
    OffsetShape,
    FilletShape,
    LayerMapShape,
)
```

- [ ] **Step 6: Write `registry.py`**

```python
"""The shape union and lookups by kind, built from :data:`kinds.KINDS`."""

from __future__ import annotations

import functools
import operator
from typing import Annotated, get_args

from pydantic import Field, TypeAdapter

from mems_sketch.core.shapes.base import Node
from mems_sketch.core.shapes.kinds import KINDS

Shape = Annotated[functools.reduce(operator.or_, KINDS), Field(discriminator="kind")]
for _kind in KINDS:  # kinds with children refer to Shape, which exists only now
    _kind.model_rebuild(_types_namespace={"Shape": Shape})

SHAPE_ADAPTER: TypeAdapter = TypeAdapter(Shape)
# Every accepted ``kind`` value, including old ones such as ``group``.
BY_KIND: dict[str, type[Node]] = {
    name: kind for kind in KINDS for name in get_args(kind.model_fields["kind"].annotation)
}
PRIMITIVE_KINDS = tuple(kind.kind_name() for kind in KINDS if kind.category == "primitive")
WRAPPERS: dict[str, type[Node]] = {op: kind for kind in KINDS for op in kind.wraps}


def kind_class(kind: str) -> type[Node]:
    try:
        return BY_KIND[kind]
    except KeyError:
        raise KeyError(f"unknown shape kind '{kind}'") from None


def default_shape(kind: str, layer: str = "device") -> Shape:
    """A new primitive of ``kind`` to start editing from."""
    cls = BY_KIND.get(kind)
    if cls is None or cls.category != "primitive":
        raise ValueError(f"unknown primitive '{kind}'")
    return cls.default(layer)


def wrap_shapes(operation: str, name: str, nodes: list[Shape]) -> Shape:
    """A new operation node named ``name`` holding ``nodes``."""
    cls = WRAPPERS.get(operation)
    if cls is None:
        raise ValueError(f"unknown operation '{operation}'")
    return cls.wrap(operation, name, nodes)
```

- [ ] **Step 7: Delegate in `tree.py`, `points.py`, `render.py`, `rewrite.py`**

`tree.py`: import `RefShape` from `kinds.ref`, `Shape` from `registry` (TYPE_CHECKING), `to_ictrans` from `geometry`; drop the other kind imports, `to_dbu` and `evaluate`. Replace `child_lists` and `placement_of`'s loop body:

```python
def child_lists(shape: Shape) -> list[list[Shape]]:
    return shape.child_lists()
```

```python
    for depth in range(1, len(path)):
        ancestor = node_at(shapes, path[:depth])
        ancestor_placement = ancestor.placement({**variables, "i": 0.0, "j": 0.0})
        if ancestor_placement is not None:
            transform = transform * to_ictrans(ancestor_placement)
    return transform
```

`points.py`: import `Point` from `base`, `Shape` from `registry` (TYPE_CHECKING); in `own_strings` replace `_CHILD_FIELDS` with `type(shape).child_fields` and delete `_CHILD_FIELDS`.

`render.py`: imports become `DBU_UM, Geometry, placement, to_dbu` from component, `evaluate`, `Repeat, RenderContext, Value` from `base` (plus `Point` if still used), `Shape` from `registry` (TYPE_CHECKING), `apply_transform, to_ictrans` from `geometry`, the points and tree imports unchanged. Replace `_render_once` and `_transform_of`:

```python
    def _render_once(
        self,
        shape: Shape,
        v: dict[str, float],
        scope: Mapping[str, NodePoints],
        path: NodePath,
    ) -> tuple[Geometry, dict[str, Point]]:
        def render_lists(lists, inner_scope) -> list[Geometry]:
            return self._render_lists(lists, v, inner_scope, path)[0]

        return shape.render(RenderContext(v, scope, self.lookup, render_lists))


def transform_of(shape: Shape, v: dict[str, float]) -> kdb.DCplxTrans:
    """The placement a node applies to its content, in µm (identity if it has none)."""
    transform = shape.placement(v)
    return kdb.DCplxTrans() if transform is None else transform
```

and in `_render_node` call `transform_of(shape, first)` instead of `_transform_of`.

`rewrite.py`: import `SHAPE_ADAPTER` and (TYPE_CHECKING) `Shape` from `registry`, `Value` from `base`; drop kind imports. Replace the `match` at the end of `translated`:

```python
def inner(children: list[Shape]) -> list[Shape]:
    nested = moving | _names(shape)
    return [translated(c, dx, dy, nested) for c in children]


return shape.model_copy(update=shape.moved(x, y, inner))
```

- [ ] **Step 8: Update the re-exports**

In `shapes/__init__.py` import (keep the existing names working):
- from `base`: `INDEX_NAMES, Align, Node, Operation, Point, Primitive, RenderContext, Repeat, Value, check_point_reference`
- from `kinds`: `KINDS` and all 11 kind classes; add `GroupShape = TransformShape  # the earlier name`
- from `registry`: `BY_KIND, PRIMITIVE_KINDS, SHAPE_ADAPTER, WRAPPERS, Shape, default_shape, kind_class, wrap_shapes`
- from `render`: add `transform_of`
- the rest as in Task 1. Update `__all__`.

Import `registry` before `tree`, `points`, `render` and `rewrite` in `__init__.py` so the models are complete before anything uses them.

- [ ] **Step 9: Run the contract test and the full suite**

Run: `uv run --extra dev python -m pytest -q -p no:cacheprovider tests/test_shape_kinds.py`
Expected: all pass (74 tests: 1 + 11 + 4×15 + 2).

Run the full suite. Expected: `275 passed` (201 + 74). Then the lint commands. Expected: clean.

- [ ] **Step 10: Commit**

```bash
git add -A src/mems_sketch/core/shapes tests/test_shape_kinds.py
git commit -m "Shape kinds as one module each, looked up in a registry (refactor 2/4)"
```

---

### Task 3: The GUI uses the kind methods

**Files:**
- Modify: `src/mems_sketch/gui/panels.py`, `gui/icons.py`, `gui/document.py`, `gui/properties.py`

- [ ] **Step 1: `panels.py`**

Replace the body of `describe` and delete `_summary`:

```python
def describe(shape: Shape) -> str:
    """Short summary shown next to a node's name, with its alignment if it has one."""
    summary = shape.summary()
    if shape.align is not None:
        summary += f" · {shape.align.point} at {shape.align.to}"
    return summary
```

```python
def shape_icon(shape: Shape) -> QIcon:
    return icons.icon(shape.icon_name())
```

- [ ] **Step 2: `icons.py`**: delete `KIND_ICONS`.

- [ ] **Step 3: `document.py`**

- Delete `default_primitive`; in `add_primitive` use `default_shape(kind, layer)` (import from `mems_sketch.core.shapes`).
- In `wrap`, replace the whole `match operation:` block with:

```python
        wrapper = wrap_shapes(operation, name, nodes)
```

- In `transform_nodes`, replace `if isinstance(node, RefShape | TransformShape):` with `if type(node).placed:`.
- Remove imports ruff reports as unused.

- [ ] **Step 4: `properties.py`**

Replace the three kind checks after the generic field loop:

```python
        fields = type(node).model_fields
        if "points" in fields:
            form.addRow("Points (x, y per line)", self._points_editor(node.points))
        if "mapping" in fields:
            form.addRow("Mapping (from → to)", self._mapping_editor(node.mapping))
        if "params" in fields:
            layout.addWidget(self._params_editor(node))
```

and in the align editor replace `if node.kind in ("ref", "transform"):` with `if type(node).placed:`.

- [ ] **Step 5: Run the full suite and lint**

Expected: `275 passed`, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/mems_sketch/gui
git commit -m "GUI asks shape kinds to describe themselves (refactor 3/4)"
```

---

### Task 4: Guard rails

**Files:**
- Modify: `tests/test_architecture.py`, `tests/test_gui_polish.py`

- [ ] **Step 1: Architecture guard**

Append to `tests/test_architecture.py`:

```python
KINDS_DIR = PACKAGE / "core" / "shapes" / "kinds"
# Asking "is this a component reference?" is about what references mean, not a
# switch over every kind, so it may happen anywhere.
ALLOWED_CLASSES = {"RefShape"}


def _class_names(node) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Tuple):
        return [name for element in node.elts for name in _class_names(element)]
    if isinstance(node, ast.BinOp):
        return _class_names(node.left) + _class_names(node.right)
    return []


def kind_switches(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        where = f"{path.relative_to(PACKAGE)}:{getattr(node, 'lineno', '?')}"
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
            and len(node.args) == 2
        ):
            names = [
                n
                for n in _class_names(node.args[1])
                if n.endswith("Shape") and n not in ALLOWED_CLASSES
            ]
            if names:
                found.append(f"{where}: isinstance(…, {' | '.join(names)})")
        elif isinstance(node, ast.MatchClass) and isinstance(node.cls, ast.Name):
            if node.cls.id.endswith("Shape"):
                found.append(f"{where}: case {node.cls.id}()")
        elif isinstance(node, ast.Compare) and isinstance(node.left, ast.Attribute):
            if node.left.attr == "kind":
                found.append(f"{where}: compares .kind")
        elif isinstance(node, ast.Match) and isinstance(node.subject, ast.Attribute):
            if node.subject.attr == "kind":
                found.append(f"{where}: match on .kind")
    return found


def test_only_the_shape_kinds_switch_on_kinds():
    """Kind-specific behaviour lives in core/shapes/kinds; elsewhere use the Node methods."""
    offenders = [
        switch
        for path in PACKAGE.rglob("*.py")
        if KINDS_DIR not in path.parents
        for switch in kind_switches(path)
    ]
    assert offenders == []
```

- [ ] **Step 2: Check the guard bites**

Temporarily add `isinstance(shape, RectShape)` somewhere in `gui/panels.py` (e.g. `_ = isinstance(shape, RectShape)` in `describe`), run
`uv run --extra dev python -m pytest -q -p no:cacheprovider tests/test_architecture.py`.
Expected: FAIL listing `gui/panels.py:NN: isinstance(…, RectShape)`. Revert the line; run again. Expected: PASS.

- [ ] **Step 3: Every kind has an icon (GUI)**

Append to `tests/test_gui_polish.py` (add the imports at the top):

```python
from mems_sketch.core.shapes import KINDS, RectShape, RefShape, wrap_shapes


def test_every_shape_kind_has_an_icon():
    rects = [RectShape(layer="device", x0=0, y0=0, x1=1, y1=1) for _ in range(2)]
    shapes = [kind.default("device") for kind in KINDS if kind.category == "primitive"]
    shapes += [wrap_shapes(op, "w", rects) for kind in KINDS for op in kind.wraps]
    shapes.append(RefShape(component="rectangle"))
    assert {s.icon_name() for s in shapes} <= set(icons.ICONS)
```

- [ ] **Step 4: Full suite and lint**

Expected: `277 passed`, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add tests/test_architecture.py tests/test_gui_polish.py
git commit -m "Test that only shape kinds switch on kinds, and that each has an icon (refactor 4/4)"
```
