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

- **Components**: the project's components (double-click to edit; ✎ marks the
  one being edited), library components and built-ins (double-click or
  **Place** to insert). New, Rename (updates every reference), Delete, Set top.
- **Shapes**: the shape tree of the component being edited. Checkboxes enable
  or disable a node; Ctrl/Shift-click selects several. Boolean operands appear
  under A and B.
- **Canvas**: wheel to zoom, middle or right drag to pan, F to fit, click to
  select. The selection is outlined in yellow and rule violations are boxed in
  red. The View box switches between drawn, as-etched and etch-compensated
  geometry.
- **Properties**: generated from the selected node's schema. Any numeric field
  takes a number or an expression, with its value shown beside it. For a
  component the component's own parameters are listed, with their declared
  defaults as placeholders.
- **Parameters**: the edited component's parameters: default (number or
  expression), min, max and the resolved value.
- **Layers**: visibility, colour, GDS numbers, undercut, minimum width and
  spacing.
- **Process constants** (tabbed with Parameters): values available in every
  expression as `process.<name>`.
- **View → Panels** reopens any panel that was closed.
- **Operations** (Group, Union, Subtract, Intersect, XOR, Offset, Fillet, Layer
  map) wrap the selected sibling shapes in a new operation node; **Edit →
  Unwrap** reverses it. **Make component** (Ctrl+K) moves the selection into a
  new component. The parameters it uses become the new component's parameters,
  so the geometry does not change.
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
| Operation | `group` | Union of children, then mirror, scale, rotate, move |
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
- **Etch loss and rule checks run on the final result**, after all operations.
- Units are micrometres. Coordinates snap to the 1 nm grid, and curves stay
  within 5 nm of the true arc.

## Code-first use

```python
from mems_sketch import ComponentDef, Instance, ParamDef, RectShape, Repeat, load, save, export
from mems_sketch.process import etch, rules

project = load("examples/resonator")
project.set_variable("pitch", 16)                     # a top-level parameter
project.define_component(ComponentDef(
    name="finger_array",
    parameters=[ParamDef(name="n", default=4, min=1, integer=True),
                ParamDef(name="w", default="process.min_gap")],
    shapes=[RectShape(layer="device", x0=0, y0=0, x1="w", y1=30,
                      repeat=Repeat(columns="n", dx="3 * w"))],
))
project.add(Instance("fingers", "finger_array", {"n": 12}, x=200))

print(rules.check(project))
save(project, "my_resonator")                         # a project folder
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
| `core/shapes.py` | The shape tree: primitives, references, operations, evaluation |
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

**New export format:** write a class with `format_name`, `file_extension` and
`export(project, geometry, path)`. Register it either with `@register_exporter`
or, from a separate package, under the `mems_sketch.exporters` entry-point group:

```toml
[project.entry-points."mems_sketch.exporters"]
svg = "my_package.svg:SvgExporter"
```
