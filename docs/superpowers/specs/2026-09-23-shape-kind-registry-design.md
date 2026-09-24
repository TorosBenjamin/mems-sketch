# Shape-kind registry

Sub-project 1 of 3 of the maintainability refactor. The others, designed
separately afterwards: 2. split `ProjectDocument` into command modules around
an undoable-transaction core; 3. tools talk to a small `Editor` interface
instead of `MainWindow`, which is split into controllers.

## Goal

Adding a shape kind is one new module plus one line in the list of kinds.
No kind-specific edit elsewhere in `core` or `gui` is needed, except drawing
its icon (optional: a fallback icon is used).

Today each kind is special-cased in 41 places across 7 files (`core/shapes.py`,
`gui/document.py`, `gui/panels.py`, `gui/properties.py`, `gui/icons.py`, …).

## Constraints

- Behaviour does not change.
- The YAML format does not change, including the legacy `kind: group`, which
  is read as `transform`.
- Every name importable from `mems_sketch.core.shapes` today stays
  importable from there.
- The existing tests pass unchanged, except import lines of private helpers
  that moved.
- No Qt in `core` (enforced by `tests/test_architecture.py`).

## Layout

`core/shapes.py` becomes a package:

```
core/shapes/
  __init__.py      re-exports the public names of the old module
  base.py          Node base class, Align, Repeat, Value, RenderContext
  registry.py      builds the `Shape` union from KINDS; lookups by kind
  tree.py          walk, paths, node_at, container_of, placement_of, find, references
  points.py        NodePoints, point names, point dependencies
  render.py        Evaluator: repeat, alignment, scopes, cache; geometry per kind
                   is delegated to the kind
  rewrite.py       translated, map_expressions, rename_node_references, offset_value
  geometry.py      shared helpers: arc points, segment counts, booleans, paths
  kinds/
    __init__.py    KINDS = [RectShape, PolygonShape, …]   (the one line to add)
    rect.py polygon.py circle.py arc.py path.py
    ref.py transform.py boolean.py offset.py fillet.py layer_map.py
```

Dependencies point one way: `kinds/*` import only `base` and `geometry`;
`registry` imports `kinds`; `tree`, `points`, `render` and `rewrite` import
`registry` and `base`.

`registry.py` builds the discriminated union from the kinds and rebuilds the
models that contain children with it:

```python
Shape = Annotated[Union[tuple(KINDS)], Field(discriminator="kind")]
for kind in KINDS:
    kind.model_rebuild(_types_namespace={"Shape": Shape})
```

A spike confirmed this parses nested children, keeps the `group` alias and
rejects unknown kinds.

## The kind interface

A kind is its pydantic model plus these members. `Node` provides the defaults.

| Member | Replaces | Default |
|---|---|---|
| `category: ClassVar[str]`: `"primitive"`, `"operation"` or `"reference"` | `PRIMITIVE_KINDS` | required |
| `icon: ClassVar[str]` | `icons.KIND_ICONS` | `"point"` |
| `render(self, ctx) -> tuple[Geometry, dict[str, Point]]` | the `match` in `Evaluator._render_once` | required |
| `child_lists(self) -> list[list[Shape]]` | `child_lists()` | `[]` |
| `moved(self, x, y, inner) -> dict` | the `match` in `translated()` | children moved by `inner` |
| `placement(self, v) -> DCplxTrans \| None` | `Ref \| Transform` checks: `_transform_of`, `placement_of`, `document.transform_nodes`, the properties note | `None` |
| `summary(self) -> str` | `panels._summary` | the kind |
| `icon_name(self) -> str` | `panels.shape_icon` | `icon` |
| `default(cls, layer)` (primitives) | `document.default_primitive` | none |
| `wraps: ClassVar[tuple[str, ...]]` and `wrap(cls, op, name, nodes)` (operations) | the `match` in `document.wrap` | none |

Two more ClassVars keep generic code generic: `child_fields` names the
fields holding child lists (`child_lists`, `moved` and `own_strings` use it),
and `placed` marks kinds with x, y, rotation and mirroring (reference and
transform). `Primitive` and `Operation` are small base classes setting the
category, the primitive summary (`kind · layer`) and `child_fields =
("children",)`.

In `moved`, `x` and `y` move one coordinate value each (they already skip
values that follow a moving node), and `inner` moves a list of children. The
alignment case stays generic in `rewrite.translated`.

`RenderContext` gives a kind what it needs without the Evaluator's internals:
`ev(value)`, `children(lists, scope=None)`, `lookup(component)` and `scope`.

Kept on purpose: checks that ask whether a node is a component reference
(`references()`, renaming a component in `core/project.py`, opening,
making and unpacking components in `gui/document.py`). They are about what a
reference means, not a switch over all kinds, and stay `isinstance(x, RefShape)`.

## GUI

- `panels.describe` uses `summary()`; `panels.shape_icon` uses `icon_name()`.
- `document.default_primitive` and `document.wrap` look kinds up in the
  registry.
- `properties.py` picks editors by field, not by kind: a `points` field gets
  the points editor, `mapping` the mapping editor, `params` the parameters
  editor. The "x and y do not move it while aligned" note shows for kinds
  whose `placement` is not `None`.
- `icons.KIND_ICONS` goes; the icon name comes from the kind.

## Steps

One commit each, with the full test suite green:

1. Pure move: the package with the old code moved unchanged into `tree`,
   `points`, `render`, `rewrite` and `geometry`, and `__init__` re-exporting.
2. `Node`, the registry and `kinds/` with `render`, `child_lists`, `moved`
   and `placement`; the matching `match` blocks go.
   The kinds get their whole interface here, `summary`, `icon_name`,
   `default` and `wrap` included, so each kind file is written once.
3. `panels`, `document`, `properties` and `icons` use `summary`,
   `icon_name`, `default` and `wrap`.
4. Guard rails (below).

## Testing

- The existing tests are the safety net.
- `tests/test_shape_kinds.py`, parametrized over the registered kinds:
  - a YAML round trip leaves each kind unchanged;
  - primitives: `default(layer)` renders non-empty geometry on that layer,
    and moving by (dx, dy) shifts its bounding box by exactly (dx, dy);
  - operations: `wrap()` around a rectangle renders;
  - `summary()` and `icon_name()` return non-empty strings; a GUI test checks
    the icon exists.
- `tests/test_architecture.py` fails when code outside `core/shapes/kinds/`
  matches on concrete shape classes or kind strings, with the `RefShape`
  checks above as the allowlist.

## Out of scope

- Sub-projects 2 and 3.
- Third-party kinds through entry points, as exporters work. Easy to add
  later on top of the registry; not needed now.
