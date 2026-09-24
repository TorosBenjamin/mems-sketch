# Python API

Two levels: the **project model** for building a design in code, and an
**edit session** for changing one the way the editor does (with undo,
checking and the same commands).

## Building a design in code

```python
from mems_sketch import (
    Align,
    ComponentDef,
    Instance,
    ParamDef,
    RectShape,
    Repeat,
    export,
    load,
    save,
)
from mems_sketch.process import rules

project = load("examples/resonator")
project.set_variable("pitch", 16)  # a parameter of the top component
project.define_component(
    ComponentDef(
        name="finger_array",
        parameters=[
            ParamDef(name="n", default=4, min=1, integer=True),
            ParamDef(name="w", default="process.min_gap"),
        ],
        shapes=[
            RectShape(
                layer="device",
                x0=0,
                y0=0,
                x1="w",
                y1=30,
                repeat=Repeat(columns="n", dx="3 * w"),
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

print(rules.check(project))  # design-rule violations
save(project, "my_resonator")  # a project folder (or "my_resonator.json": one file)
export(project, "my_resonator.gds")
```

- `load` takes a project folder, its `project.yaml`, a one-file project
  (`.json`, `.xml`, `.mat`, `.yaml`) or a legacy `.mems` file.
- `project.render(component=None, params=None)` returns the `Geometry` (KLayout
  regions per layer); `export(project, path, component=…, params=…)` writes it.
- `examples/build_examples.py` generates the example library and project from
  code.

## Editing like the editor does

```python
from mems_sketch.editing import EditSession

session = EditSession.open_project("examples/resonator")
session.set_active("suspension")  # like switching tabs
session.components.make([((0, 0),), ((0, 1),)], "spring_with_anchor")
session.parameters.update("turns", default=4)
session.undo()
session.save()
```

- A shape is addressed by its **path**: one `(slot, index)` step per level,
  the slot picking the parent's child list (a boolean's `a` is 0, `b` is 1).
  `((0, 2),)` is the third top-level shape.
- Every command is one undo step and is **checked**: if it would stop a
  project that built from building, it is rolled back and raises
  `ValueError` with the reason.
- Command groups: `components` (new, rename, make, unpack, libraries…),
  `nodes` (add, replace, remove, wrap…), `moves`, `modifiers`, `corners`,
  `points`, `parameters`, `process`, `imports`, `history`.
- `session.results` is what the components evaluate to: `geometry()`,
  `scope(path)`, `node_box(path)`, points…

A few more:

```python
session.imports.add("padframe.gds", cell="FRAME")  # an imported layout
session.corners.add(((0, 0),), 10, 4, radius=2)  # round the corner at (10, 4)
session.history.uncommitted().changes  # what changed since the last commit
session.history.commit_changes("Longer beams")
session.export("out.mat")  # the active component's geometry
```

## From MATLAB

- **The command line:** `system('mems-sketch-cli export design out.mat --set
  pitch=15')`, then `load('out.mat')`; loops over `--set` values give
  parameter sweeps.
- **The Python API** through MATLAB's Python interface:
  `p = py.mems_sketch.load("examples/resonator");`.
- **Geometry from MATLAB:** save a struct with `format =
  'mems-sketch-geometry/1'` and `layers.<name>.polygons` (N×2 matrices) and
  import it (see [file formats](file-formats.md#geometry-documents)).
