# mems-sketch

Parametric MEMS layout design in Python. Everything is a component: a design
is built from parametric components whose values can be expressions over
parameters and process constants. The tool applies etch-loss compensation,
checks design rules, and exports through pluggable format modules. Simulation
is out of scope.

![MEMS Sketch GUI](docs/screenshot.png)

## Install and run

```bash
pip install -e ".[gui]"                        # or ".[dev]" for tests and linting
mems-sketch examples/resonator/project.yaml    # the GUI (or: python -m mems_sketch.gui)
mems-sketch-cli check examples/resonator       # the same compiler, from the command line
```

## Architecture

```
  project folder (YAML)  ──►  backend = compiler  ──►  geometry, rule checks, exports
  source of truth, in git      mems_sketch.core …            │
          ▲                          ▲                        ▼
          └──── edits ──── frontends: GUI (mems_sketch.gui), CLI, Python scripts
```

- **Source:** a project folder of YAML files (see below). It is small,
  readable, and gives meaningful git diffs.
- **Backend:** reads a project, resolves parameters, builds geometry, applies
  etch loss, checks rules and exports. It has no GUI dependency, which CI
  enforces (`tests/test_architecture.py`).
- **Compiler cache:** every component build is keyed by a fingerprint of
  everything it depends on (definition, referenced components, parameter
  values, process constants). Identical instances are built once, and after an
  edit only the changed components and the components that contain them are
  rebuilt. The cache is in memory; the keys are designed so that it can later
  be persisted, e.g. in SQLite, for large layouts.
- **Frontends:** the GUI edits through the backend's API and displays compiled
  results. The CLI and Python scripts work on the same project files.

## Projects

```
my_project/
  project.yaml        format, name, top component, libraries
  process.yaml        layers (GDS numbers, undercut, rules) and process constants
  components/
    top.yaml          one file per component; the design itself is the top component
    suspension.yaml
```

Files are always written the same way: default values are left out, keys stay
in a fixed order, and unchanged files are not rewritten. Changing one value
changes one line. See `examples/resonator` and the library it uses,
`examples/libraries/mems_std`.

### Everything is a component

- A component has **parameters** (with defaults, limits and optional integer
  constraint) and a **shape tree**. A default may be an expression over other
  parameters, e.g. `hole_r` defaulting to `pitch / 6`.
- The design is the **top** component. Its parameters play the role of global
  variables, so any project can be placed inside another one.
- Values passed to a component are evaluated in the caller's scope. Parameters
  left out fall back to their defaults.
- **Process constants** from `process.yaml` are available in every expression
  as `process.<name>`, so process values do not have to be passed down.

### Alignment points

Instead of calculating positions by hand, place shapes relative to each other:

```yaml
- kind: ref
  name: anchor
  component: anchor
  align: {point: bottom, to: spring.end, dy: -1}   # sit on the spring, 1 µm overlap
```

- **Every shape** has bounding-box points: `center`, `left`, `right`, `top`,
  `bottom`, `top_left`, `top_right`, `bottom_left`, `bottom_right`.
- **Components can declare points** (the Points panel, or `points:` in the
  component file), e.g. where a spring ends. A point's position may be
  measured from one of the component's own shapes (`at: spring.end`), which is
  how a component passes on a point of a part inside it. Built-ins have some:
  `serpentine_spring` has `start` and `end`, `comb_drive` has `moving` and
  `fixed` (the outer edge of each spine).
- **`align`** moves a shape so that its `point` lands on `to` (plus `dx`,
  `dy`). It is a rule, not a one-off move: it is re-evaluated after every
  change, so the parts stay attached when parameters change.
- **In expressions**, point coordinates are available as `<shape>.<point>.x`
  and `.y`, e.g. `x1: base.right.x - 5`.
- A shape can use the points of the named shapes in its own list, in the other
  operand of a boolean, and in every enclosing list. Inside a transform they
  are seen in the transform's own coordinates. Shapes are evaluated in the
  order their alignments need; an alignment loop is reported as an error.

### Libraries

A library is a folder of components (any project folder works), listed in
`project.yaml`:

```yaml
libraries:
  std: ../libraries/mems_std
```

Its components are placed as `std.perforated_plate`. Libraries are read-only
and self-contained: inside a library, bare names refer to that library's own
components (then built-ins), never to the project's.

## Command line

```bash
mems-sketch-cli new     my_project
mems-sketch-cli info    my_project
mems-sketch-cli check   my_project [--component NAME] [--set pitch=15] [--etch etched] [--json]
mems-sketch-cli export  my_project out.gds [--etch compensated] [--set pitch=15]
mems-sketch-cli convert old_design.mems my_project     # import the earlier SQLite format
```

`check` exits with status 1 when there are rule violations, so it can gate CI.
Errors exit with status 2. `--set` accepts numbers or expressions and can be
repeated, which is convenient for parameter sweeps and for calling from
MATLAB with `system(...)`.

## GUI

- **Tabs**: every component opens in its own tab, with its own zoom, selection
  and view mode; the panels show the current tab. Double-click a component in
  the Components panel, or a placed component in the canvas or the Shapes
  tree, to open it. Library and built-in components open read-only. **View →
  Split view** (Ctrl+\\) shows two tabs side by side: edit a spring on one side
  and watch the resonator that uses it on the other. A `*` on a tab marks a
  component changed since the last save. Open tabs are remembered per project
  (in the app's settings, not in the project folder).
- **Undo/redo** is one history for the whole project and goes back to the tab
  where the change was made, reopening it if it was closed.
- **Components**: the project's components (✎ marks the one in the current
  tab), library components and built-ins. **Place** inserts the selected one.
  New, Rename (updates every reference and tab), Delete, Set top.
- **Shapes**: the shape tree of the component being edited. Checkboxes enable
  or disable a node; Ctrl/Shift-click selects several. Boolean operands appear
  under A and B, and each alignment is shown (e.g. `bottom at spring.end`).
- **Canvas**: wheel to zoom, middle or right drag to pan, F to fit, click to
  select. The selection is outlined with its alignment points, rule
  violations are boxed in red and the component's own points are marked in
  green. The background is white; **View → Dark canvas** switches to dark
  (remembered). The View box switches between drawn, as-etched and etch-compensated
  geometry.
- **Properties**: generated from the selected node's schema. Any numeric field
  takes a number or an expression, with its value shown beside it. For a
  component the component's own parameters are listed, with their declared
  defaults as placeholders.
- **Parameters**: the current tab's parameters: default (number or
  expression), min, max, trial and the resolved value. A **trial** value shows
  the component with another value without changing the design: it is not
  saved or undone, and only affects that component's own tab (the components
  that place it still pass their own values). It also works on library and
  built-in components.
- **Points** (tabbed with Parameters): the points the edited component
  declares for whoever places it.
- **Moving**: drag shapes on the canvas, or nudge the selection with the arrow
  keys (one grid step; Shift for a tenth). The drag snaps to the grid, or to
  another shape's point when one is near (Ctrl: no snapping). Shapes aligned
  to the dragged ones move along. What changes is what the shape stores,
  always relative to its parent: `x`, `y` of a component or transform, the
  coordinates of a primitive, or the offset of an aligned shape (Alt-drag
  removes the alignment instead). Expressions stay parametric: `plate/2 + 39`
  moved by 11 becomes `plate/2 + 50`. Releasing with **Shift** on a snapped
  point aligns the shape there. Esc cancels; each drag is one undo step.
- **Align** (Ctrl+L): select a shape, click one of its points, then click the
  point to put it on. Fine-tune the offset in Properties; **Edit → Remove
  alignment** takes it off and leaves the shape where it is. Esc cancels.
- **Layers**: visibility, colour, GDS numbers, undercut, minimum width and
  spacing.
- **Process constants** (tabbed with Parameters): values available in every
  expression as `process.<name>`.
- **View → Panels** reopens any panel that was closed.
- **Operations** (Union, Subtract, Intersect, XOR, Offset, Fillet, Layer map,
  Transform) wrap the selected sibling shapes in a new operation node; **Edit →
  Unwrap** reverses it. **Make component** (Ctrl+K) moves the selection into a
  new component. The parameters it uses become the new component's parameters,
  so the geometry does not change; a single transform becomes a component
  placed where the transform was. **Unpack component** (Ctrl+Shift+K) does the
  opposite: it replaces a reference by a transform holding a copy of the
  component's shapes, with the parameter values filled in.
- Renaming a shape updates the alignments and expressions that use it.
- Every edit is a transaction: if it would stop the project from compiling
  (including components that use the edited one), it is rolled back with a
  message. Full undo/redo.
- **File**: open a project (or a legacy `.mems` file), save to a folder,
  export drawn, as-etched or etch-compensated geometry.

## Shapes and operations

A component's body is a **shape tree** that is re-evaluated whenever a value
changes. Operations are nodes in the tree, not destructive edits, so a
subtraction stays editable and parametric.

| Kind | Node | Notes |
|---|---|---|
| Primitive | `rect`, `polygon`, `circle`, `arc`, `path` | `arc` is an annular sector (a ring at 360°); `path` is a centreline with a width and flush/square/round ends |
| Reference | `ref` (or `Instance(...)`) | A component with parameters and placement |
| Operation | `transform` | Mirror, scale, rotate and move the children as one piece (formerly `group`, still read) |
| | `boolean` | `a` union / subtract / intersect / xor `b` |
| | `offset` | Grow (+) or shrink (−) outlines |
| | `fillet` | Round convex (`radius`) and concave (`inner_radius`) corners |
| | `layer_map` | Move geometry between layers, e.g. derive an anchor layer from a device outline |

- **Booleans, offsets and fillets act per layer.** Subtracting a device-layer
  hole only affects the device layer. To combine different layers, bring them
  onto one layer with `layer_map` first.
- **Any node can repeat on a grid** (`repeat=Repeat(columns, rows, dx, dy)`),
  with `i` and `j` as the column and row index.
- **`enabled=False`** skips a node without deleting it.
- **Any node can be aligned** (`align=Align(point, to, dx, dy)`), see
  [Alignment points](#alignment-points).
- **Transform or component?** Make a component when something is reused or
  deserves its own parameters. Use a transform to move, rotate or mirror a few
  shapes together once.
- **Etch loss and rule checks run on the final result**, after all operations.
- Units are micrometres. Coordinates snap to the 1 nm grid, and curves stay
  within 5 nm of the true arc.

## Code-first use

```python
from mems_sketch import (
    Align,
    ComponentDef,
    Instance,
    ParamDef,
    RectShape,
    Repeat,
    load,
    save,
    export,
)
from mems_sketch.process import etch, rules

project = load("examples/resonator")
project.set_variable("pitch", 16)  # a top-level parameter
project.define_component(
    ComponentDef(
        name="finger_array",
        parameters=[
            ParamDef(name="n", default=4, min=1, integer=True),
            ParamDef(name="w", default="process.min_gap"),
        ],
        shapes=[
            RectShape(
                layer="device", x0=0, y0=0, x1="w", y1=30, repeat=Repeat(columns="n", dx="3 * w")
            )
        ],
    )
)
project.add(
    Instance(
        "fingers",
        "finger_array",
        {"n": 12},
        align=Align(point="bottom_left", to="mass.top_right", dx=10),
    )
)

print(rules.check(project))
save(project, "my_resonator")  # a project folder
export(project, "my_resonator.gds", geometry=etch.compensated(project))
```

`examples/build_examples.py` generates the example library and project from
code. MATLAB can use the same API through its Python interface
(`p = py.mems_sketch.load("examples/resonator");`) or call the CLI.

## Package layout

| Module | Contents |
|---|---|
| `core/project.py` | `Project`, `Library`, `Instance`: components, name resolution, parameters |
| `core/compiler.py` | `Compiler` and `Session`: fingerprints, cache, rendering |
| `core/shapes.py` | The shape tree: primitives, references, operations, alignment, evaluation |
| `core/user_component.py` | `ComponentDef`, `ParamDef` and their adapter to `Component` |
| `core/component.py` | `Component` base class, `Geometry`, built-in component registry |
| `core/process.py` | `Process`, `Layer`, process constants |
| `core/expressions.py` | Safe arithmetic expressions with dependency resolution |
| `components/library.py` | Built-ins: `rectangle`, `anchor`, `comb_drive`, `serpentine_spring` |
| `process/etch.py`, `process/rules.py` | Etch loss (predict / compensate) and design-rule checks |
| `storage/` | Project folders (canonical YAML) and the legacy SQLite importer |
| `export/` | Exporter plugins: GDSII, OASIS, DXF |
| `cli.py` | `mems-sketch-cli` |
| `gui/` | PySide6 frontend: document (transactions, undo), canvas, panels, property editor |

## Extending

**New built-in component:** subclass `Component`, define a nested `Params`
model and `build()`, and decorate the class with `@register_component`.
Override `points()` to offer alignment points.

**New export format:** write a class with `format_name`, `file_extension` and
`export(project, geometry, path)`. Register it either with `@register_exporter`
or, from a separate package, under the `mems_sketch.exporters` entry-point group:

```toml
[project.entry-points."mems_sketch.exporters"]
svg = "my_package.svg:SvgExporter"
```
