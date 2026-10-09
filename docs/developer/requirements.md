# Requirements

What mems-sketch must do, and how well. It covers what the tool already does
as well as what is new. The architecture
([Architecture](architecture.md) for the code as it is,
[Geometry core architecture](core-architecture.md) for where it is going) has
to satisfy every requirement here. A rewrite that drops one has a bug.

**How to read it.** Each requirement has an ID, a priority and a status:

- **Priority:** **Must** (the tool is not done without it), **Should**
  (planned, can come after the Musts), **Could** (wanted when there is
  room), **Later** (recorded so the architecture leaves room for it).
- **Status:** **exists** (the current tool does it; the
  [user guide](../user/README.md) shows how), **new**, or **changes**
  (exists, but the requirement asks for something different).

Requirements are written to be testable: each one should map to a test.
Numbers still to be decided are marked **TBD** and listed under
[Open questions](#open-questions).

## Scope

mems-sketch is for **designing MEMS layouts parametrically**: a design is
built from components whose values are parameters and expressions, so
changing a value redraws everything that depends on it. Its outputs are
masks for fabrication and geometry for simulation.

**Out of scope:**

- simulation itself (solving, post-processing);
- process steps after the design: etch compensation, mask preparation,
  process simulation;
- editing a mesh element by element (meshes are controlled by their
  settings, which stay parametric: [MSH](#meshing));
- an internal precision the user can choose ([decision P-1](#decisions)).

## Users and workflows

The users are MEMS designers who know their process and simulation tools
but should not have to program. Scripting is there for those who want it.

| ID | Workflow | Requirement areas |
|---|---|---|
| W1 | Sketch a device: draw shapes on layers, place components | [Shapes](#shapes), [Editing](#editing) |
| W2 | Make it parametric: parameters, expressions, alignments | [Parameters](#components-and-parameters), [Expressions](#expressions), [Points](#points-and-alignment) |
| W3 | Reuse: components, libraries, arrays, variants | [Components](#components-and-parameters), [Modifiers](#modifiers), [Projects](#projects-and-files) |
| W4 | Check against the process rules | [Rule checks](#rule-checks) |
| W5 | Export masks for fabrication | [Export](#export) |
| W6 | Export geometry or a mesh for simulation | [Export](#export), [Meshing](#meshing) |
| W7 | Track changes and work together through git | [History](#history), [Projects](#projects-and-files) |
| W8 | Automate: parameter sweeps, generated designs, CI | [Command line and scripting](#command-line-and-scripting) |
| W9 | Evaluate a design: dimensions, gaps, mass and inertia, cross-sections | [Measurements](#measurements), [Cross-sections](#cross-sections) |

## Functional requirements

### Projects and files

- **PRJ-1** (Must, exists) A project is a **folder of small text files**:
  `project.yaml` (name, top component, libraries, imports), `process.yaml`
  (layers, constants), one YAML file per component under `components/`
  (private components in their owner's folder), and copies of imported
  files under `imports/`.
- **PRJ-2** (Must, exists) The YAML is **canonical**: saving the same project
  twice gives byte-identical files. Values equal to their default are left
  out, keys keep a fixed order, whole numbers have no `.0`, lists of plain
  values are on one line, and unchanged files are not rewritten. Changing one
  value changes one line.
- **PRJ-3** (Must, exists) Files are versioned (`format: mems-sketch/1`).
  Older files keep loading: `repeat:` reads as an array modifier, `group` as
  `transform`, and a layer's old `undercut` is ignored.
- **PRJ-4** (Must, exists) A project without a top component is a
  **library**. A project lists libraries by name and relative path; their
  components are placed as `name.component` and are read-only. A library
  component can be copied into the project, where it stays editable and
  keeps using the library's other components.
- **PRJ-5** (Must, exists) A whole project also fits in **one file**: JSON,
  XML, MATLAB `.mat` or YAML, imported layouts included. A folder converted
  to a file and back gives the same files, byte for byte.
- **PRJ-6** (Should, exists) Legacy `.mems` designs open as a copy and can be
  saved as a folder.
- **PRJ-7** (Should, exists) **How the project was being viewed** comes back
  on reopening: tabs, split, zoom and position per tab, selections, rulers,
  collapsed items, hidden layers, trial values, the drawing layer. It is kept
  in `.mems-sketch/` in the project folder and never committed.

### Process

- **PRC-1** (Must, exists) The process has **constants** (`process.<name>` in
  every expression) and **layers**. Each layer has a GDS layer and datatype,
  and optionally a minimum width and minimum spacing.
- **PRC-2** (Should, new) Each layer has a **position in the stack**: its
  bottom z and thickness, as values or expressions over process constants.
  It is used by 3D output ([MSH](#meshing), [VIEW](#3d-view)) and ignored by
  2D output.
- **PRC-3** (Later, new) Rules between layers (enclosure, overlap).
- **PRC-4** (Should, new) Each layer can have a **material density**, so
  measurements give mass ([MEA-3](#measurements)).
- **PRC-5** (Could, new) Each layer can have a **sidewall angle**: vertical
  by default, e.g. 54.74° for KOH etching of (100) silicon. 3D output, cross-
  sections and meshes use it. It describes the shape of the walls only;
  simulating the etch stays out of scope.

### Components and parameters

- **CMP-1** (Must, exists) **Everything is a component.** The design is the
  project's top component, so any project can be placed in another. A
  component has parameters, points and a shape tree. It never stores
  geometry: it is evaluated from its parameters.
- **CMP-2** (Must, exists) **Built-in components:** `rectangle`, `anchor`,
  `comb_drive`, `serpentine_spring`, with their parameters and points
  (`serpentine_spring`: `start`, `end`; `comb_drive`: `moving`, `fixed`).
- **CMP-3** (Must, exists) Components are **shared** or **private** to
  another (`comb/finger`), and are resolved by name from the inside out: the
  component's own private components, its owner's, the shared ones, then
  built-ins. `lib.name` names a library's.
- **CMP-4** (Must, exists) **Renaming** a component, parameter, shape or point
  updates everything that refers to it.
- **CMP-5** (Must, exists) A **parameter** has a default (a number or an
  expression over other parameters and process constants), an optional
  min, max and integer flag, and is **public** (set where the component is
  placed) or **internal** (used only inside, not offered where placed).
- **CMP-6** (Must, exists) Values passed to a placed component are
  **evaluated where it is placed**. Parameters left out take their defaults.
  Values outside min/max or not whole when integer are errors.
- **CMP-7** (Must, exists) A **trial value** shows a component with another
  parameter value without changing the design. It is not saved or undone,
  affects only that component's own tab, and also works on library and
  built-in components.
- **CMP-8** (Must, exists) **Make component** turns a selection into a new
  component whose parameters are the ones the selection uses, so nothing
  moves. **Unpack component** does the opposite.
- **CMP-9** (Must, exists) Library, built-in and imported components open
  **read-only** and show their interface (geometry, description, public
  parameters, points), not how they are built, unless the user asks to see
  it.

### Expressions

- **EXP-1** (Must, exists) **Every value** of a shape, modifier, alignment,
  parameter default or placement can be a number or an expression.
- **EXP-2** (Must, exists) Expressions are **safe arithmetic**: `+ - * / //
  % **`, unary `+ -`, parentheses, numbers, `pi`, and the functions `abs`,
  `min`, `max`, `round`, `sqrt`, `sin`, `cos`, `tan`, `ceil`, `floor`.
  Anything else is an error, never executed.
- **EXP-3** (Must, exists) Names in scope: the component's parameters,
  `process.<constant>`, point coordinates (`<shape>.<point>.x`, `.y`), inside
  an array the copy's `i` and `j`, and in a modifier `self` (the shape just
  before that modifier).
- **EXP-4** (Must, exists) Expressions are evaluated in **dependency order**,
  including through points and alignments. A loop (between expressions,
  alignments or component references) is an error that names where it is.
- **EXP-5** (Must, exists) Arithmetic is IEEE double precision, so an
  expression's value is exact to about 16 significant digits before any
  geometry is built.

### Shapes

- **SHP-1** (Must, exists) **Primitives:** `rect`; `polygon`; `circle`; `arc`
  (an annular sector, a ring at 360°); `path` (a centreline with a width and
  flush, square or round ends). Each is on one layer.
- **SHP-2** (Must, exists) **References:** `ref` places a component with
  parameter values, position, rotation and mirroring.
- **SHP-3** (Must, exists) **Operations**, as nodes in the tree that keep
  their inputs editable:
  - `transform`: move, rotate, mirror and **uniformly scale** its children as
    one piece (scale > 0);
  - `boolean`: `a` subtract, intersect or XOR `b`;
  - `offset`: grow (+) or shrink (−) outlines;
  - `fillet`: round every convex corner (`radius`) and concave corner
    (`inner_radius`);
  - `layer_map`: move geometry between layers; several sources may go to one
    target; unmapped layers are dropped unless kept.
- **SHP-4** (Must, exists) **Booleans, offsets and fillets act per layer.**
  There is no union: shapes on one layer are merged.
- **SHP-5** (Must, exists) **Guides:** a line that draws nothing, but has
  points, can be aligned and can be mirrored about.
- **SHP-6** (Must, exists) A shape can be **switched off** without being
  deleted, and an operation can be **unwrapped**, giving back its inputs.
- **SHP-7** (Must, exists) A shape can be **in pieces** (a slot cut through a
  beam), and says so. Its points come from the box around all the pieces.
- **SHP-8** (Must, changes) Circles, arcs, round path ends, fillets, rounded
  corners and the round joins of offsets are **exact curves**, not polygons
  ([QP-2](#precision)). Today they are polygons within 5 nm.
- **SHP-9** (Should, new) **Paths along curves:** a path's centreline can
  have arc segments (a given radius, or tangent to the previous segment) as
  well as straight ones, with an exact width. Folded springs get true round
  turns.
- **SHP-10** (Should, new) **Variable width:** a path's width can change
  along it (linearly, or as an expression of the position along it), for
  tapered beams, tapered comb fingers and stress-relief shapes at flexure
  roots.
- **SHP-11** (Could, new) **Splines:** a polygon or path edge can be a smooth
  curve through given points (or with control points), for free-form
  outlines such as optimised spring profiles.

### Modifiers

- **MOD-1** (Must, exists) Any shape can carry a **stack of modifiers**,
  applied in order. Order matters: mirroring an array is not arraying a
  mirror.
- **MOD-2** (Must, exists) **Array:** `columns` × `rows` copies `dx`, `dy`
  apart. Each copy can depend on its column `i` and row `j`. The first copy
  sits at the shape's own place and provides the points.
- **MOD-3** (Must, exists) **Polar array:** `count` copies around a centre,
  `step` degrees apart (a full circle by default), turned with the circle or
  keeping their orientation. The copy's index is `i`.
- **MOD-4** (Must, exists) **Mirror:** the shape and its image across a
  vertical or horizontal line, both, a guide (`about: centerline`), or
  through a point (`about: mass.center`, a 180° turn).
- **MOD-5** (Must, exists) **Corners:** chosen corners rounded or chamfered,
  each with its own radius. A corner is recorded so that it follows the
  design (as a point of the shape, a neighbour's point, or the expressions
  the shape is written with). It works on a placed library component; the
  rounding belongs to the placement.
- **MOD-6** (Must, exists) Modifiers work in the frame of the list holding
  the shape, **before its alignment moves it**, so a mirror about a part's
  centre stays put when the part moves.
- **MOD-7** (Must, exists) **Apply** turns the first modifier into real
  shapes. A modifier can be switched off, reordered or removed.
- **MOD-8** (Must, exists) When arrays are stacked, the one nearest the shape
  sets `i` and `j`.

### Points and alignment

- **PNT-1** (Must, exists) **Every shape** has the points of its bounding box:
  `center`, `left`, `right`, `top`, `bottom`, `top_left`, `top_right`,
  `bottom_left`, `bottom_right`.
- **PNT-2** (Must, exists) **A component declares points** for whoever places
  it, as expressions. A point of a part inside can be **re-exported** as the
  component's own, so it follows that part.
- **PNT-3** (Must, exists) **Alignment** is a rule, not a move: a shape's
  `point` lands on another's (`to: spring.end`), plus an offset `dx`, `dy`.
  It is re-evaluated after every change, so parts stay attached. The shape's
  own position then only matters through rotation and mirroring.
- **PNT-4** (Must, exists) Removing an alignment leaves the shape where it
  is.
- **PNT-5** (Could, new) **Points of the exact geometry** as well as of the
  bounding box: the centre of an arc, the midpoint of an edge, the
  intersection of two edges, the point where a line is tangent to a curve.
  They can be aligned to and used in expressions, and they follow the design
  like other points.

### Measurements

Measuring the design, on the exact geometry, in three places: a tool on the
canvas, a panel, and expressions.

- **MEA-1** (Must, exists) The **Measure** tool gives the distance, dx and dy
  between two points it snaps to, and **Measure angle** the angle between
  two lines. Rulers stay until cleared.
- **MEA-2** (Should, new) The Measure tool also measures **between shapes**:
  the exact minimum distance between two shapes (or two edges) on a layer,
  the length of an edge, and the radius and centre of an arc. A measurement
  can be kept on the canvas as a ruler that follows the design when it
  changes.
- **MEA-3** (Should, new) An **information panel** shows the properties of the
  selection, per layer: area, perimeter, bounding box, centroid and second
  moments of area; with the layers' thickness (PRC-2) and density (PRC-4),
  also volume, mass and moments of inertia. For several selected shapes it
  shows each and their total.
- **MEA-4** (Should, new) **Measurements in expressions:** a shape's
  measurements can be used like its points, e.g. `mass.area`,
  `mass.centroid.x`, `mass.mass`, `mass.inertia_z`, and functions such as
  `gap(finger, stator)` (minimum distance) and `overlap(rotor, stator)`
  (length or area of overlap). A design can then be driven by them: a
  counterweight sized so that the centroid sits on the pivot. Dependency
  order and loop errors apply as for other values (EXP-4).
- **MEA-5** (Could, new) Measurements are available from the command line and
  the Python API (`mems-sketch-cli info --measure`), for sweeps and reports.

### Cross-sections

- **XS-1** (Could, new) **A cross-section** of the layer stack along a line
  drawn on the canvas: the exact profile of each layer (with its thickness,
  z position and sidewall angle), shown in a view of its own and updated as
  the design changes.
- **XS-2** (Could, new) A cross-section line is saved with the component and
  can be aligned like a guide, so it stays where it matters (e.g. through a
  comb's fingers) when the design changes.
- **XS-3** (Could, new) Cross-sections can be exported as a drawing (SVG,
  DXF) for documentation and reviews.

### Editing

The GUI is described in the [user guide](../user/README.md). These are the
behaviours every frontend must keep.

- **EDT-1** (Must, exists) **Non-destructive:** every operation and modifier
  stays editable; nothing is flattened unless the user applies it.
- **EDT-2** (Must, exists) **Moves keep things parametric:** a move changes
  what a shape stores relative to its parent (`plate/2 + 39` moved by 11
  becomes `plate/2 + 50`). An aligned shape keeps its alignment and changes
  its offset.
- **EDT-3** (Must, exists) **Every edit is checked:** if it would stop a
  project that built from building (including the components that use the
  edited one), it is rolled back exactly and the reason is shown.
- **EDT-4** (Must, exists) **One undo history** for the whole project, which
  returns to the tab where the change was made. A drag of a value is one undo
  step.
- **EDT-5** (Must, exists) **Live feedback:** dragging a value redraws the
  canvas as the value changes ([QS-1](#performance-and-scale)).
- **EDT-6** (Must, exists) The same edits are available from code, with the
  same checks and undo ([CLI-3](#command-line-and-scripting)).
- **EDT-7** (Must, changes) **Snapping in the editor** (to points and to a
  grid) is a convenience of the editor, set by the user. It is separate
  from the geometry's precision and from an export's grid.
- **EDT-8** (Should, new) **What a value affects:** selecting or hovering a
  parameter (or a process constant, or a shape's value) highlights everything
  that depends on it: shapes, points, alignments and other parameters,
  directly or through other values. It comes from the dependency graph
  (QS-7), so it is shown without building anything.
- **EDT-9** (Could, new) **The change a value makes:** while a value is
  dragged or tried, the canvas can show the geometric difference from where
  it started (material added and removed, as in the History panel, HIS-2).

### Rule checks

- **DRC-1** (Must, exists) Minimum **width** and **spacing** per layer, as set
  in the process.
- **DRC-2** (Must, exists) The check runs on the final geometry **after every
  change**. Violations are listed and marked on the canvas, and each can be
  clicked to go there.
- **DRC-3** (Must, changes) The check runs on the geometry **as it will be
  exported** (snapped to the export grid, curves split at the export's chord
  tolerance), because that is what the fab checks.
- **DRC-4** (Must, exists) From the command line, `check` exits with status 1
  on violations and 2 on errors, so it can gate CI.
- **DRC-5** (Could, new) **Pattern density** per layer: the fraction of area
  covered, overall and as a map over a grid, with optional minimum and
  maximum limits as a rule. Etch rates (e.g. in DRIE) depend on it.

### Import

- **IMP-1** (Must, exists) Import **one cell of a GDS or OASIS file**, with
  its sub-cells flattened, as a read-only component.
- **IMP-2** (Must, exists) Import a **geometry document** (JSON, XML, `.mat`),
  read leniently: a polygon may be a bare N×2 matrix or `{hull, holes}`.
- **IMP-3** (Must, exists) Each imported layer goes to the project layer with
  the same GDS numbers, a new layer, or is left out.
- **IMP-4** (Must, exists) The project keeps a **copy** of the imported file.
  **Re-import** replaces it and every placement follows. An imported
  component is placed, arrayed, aligned and rounded like any other.
- **IMP-5** (Could, new) **DXF with true curves:** arcs and circles in a DXF
  file are imported as exact curves, not polygons.
- **IMP-6** (Later, new) **STEP and IGES import** of 3D parts (a package, a
  mechanical part) to check how a design fits with them, in the 3D view.
- **IMP-7** (Could, new) Imported geometry that is slightly broken (tiny
  gaps, self-touching outlines) is **repaired** where that is unambiguous, and
  what was repaired is reported.

### Export

- **OUT-1** (Must, exists) Export a component, with its current trial
  values, to **GDSII, OASIS and DXF**, with the layers' GDS numbers.
- **OUT-2** (Must, changes) Each export to a grid **chooses its grid**
  (default 1 nm) and **chord tolerance** for curves (default 5 nm).
  Neither changes the design.
- **OUT-3** (Must, new) An export to a grid **reports what snapping
  changed**: features that collapsed, gaps that opened or closed, widths
  that changed by more than half a grid step.
- **OUT-4** (Should, new) GDS and OASIS exports **keep the hierarchy**:
  components as cells, arrays as array references.
- **OUT-5** (Must, exists) Export a component's **geometry as data** (JSON,
  XML, `.mat`): polygons per layer with their holes, in µm, plus the
  component's points and parameter values.
- **OUT-6** (Could, new) Export **STEP**: the layers extruded through the
  stack into solids, with exact curves. For simulation tools that import CAD
  and mesh it themselves, and for mechanical CAD (packaging, assemblies).
  See [decision X-1](#decisions).

### Meshing

- **MSH-1** (Should, new) Export a **mesh** of a component for simulation:
  2D (the layers' faces) or 3D (the layers extruded through the stack).
- **MSH-2** (Should, new) **Mesh settings are project data**, as expressions:
  a global size, sizes per layer, and refinement regions (areas, points,
  edges, distances from edges) drawn on the canvas. They are saved, diffed
  and parametric like everything else.
- **MSH-3** (Should, new) The mesh is generated from the **exact geometry**,
  not the export grid, with its own merge tolerance (e.g. 10⁻⁶ µm).
- **MSH-4** (Should, new) Mesh regions and boundaries are **named** after
  layers and named shapes (physical groups), so a solver finds anchors and
  electrodes by name.
- **MSH-5** (Should, new) Mesh formats for common solvers (TBD: the solvers
  to support decide the formats).
- **MSH-6** (Could, new) A **preview** of the mesh on the canvas, with element
  count and quality.

### 3D view

- **VIEW-1** (Could, new) A 3D view of a component: the layers extruded
  through the stack, updated as the design changes.

### History

- **HIS-1** (Must, exists) For a project in a git repository, show **what
  changed** between versions **in words**, per component (`parameter slot_x:
  default 45 → 60`, `shape anchor_2 added`). Clicking a change opens its
  component and selects the shape.
- **HIS-2** (Must, exists) Show the change **on the canvas**: material added
  and removed, per layer.
- **HIS-3** (Must, exists) **Commit** from the editor, with a suggested
  message. Only the project folder is committed. Files staged elsewhere stay
  staged and are named.
- **HIS-4** (Must, exists) **Restore** a project or one component as it was in
  a commit, as an ordinary edit that can be undone.
- **HIS-5** (Should, exists) Initialize a repository for a saved project.
  Branches, merging, push and pull stay with the user's git tools.

### Command line and scripting

- **CLI-1** (Must, exists) `mems-sketch-cli` with `new`, `info`, `check`,
  `export` and `convert`. `--set name=value` (numbers or expressions,
  repeatable) overrides parameters, for sweeps.
- **CLI-2** (Must, exists) A **Python API** to build and change projects in
  code: load, define components, place, check, save, export.
- **CLI-3** (Must, exists) An **edit session** API that changes a project the
  way the editor does: the same commands, checks and undo.
- **CLI-4** (Should, exists) Usable **from MATLAB**: through the command line,
  MATLAB's Python interface, and geometry documents in `.mat`.
- **CLI-5** (Could, new) The geometry library usable **on its own** from
  Python and C++, without projects
  ([core architecture](core-architecture.md#overview)).

## Quality requirements

### Precision

- **QP-1** (Must, changes) The geometry has **one internal precision of
  10⁻⁸ µm or finer**, everywhere, for every design size the tool supports
  ([QS-3](#performance-and-scale)). It is fixed, not a setting
  ([decision P-1](#decisions)). Today it is 1 nm.
- **QP-2** (Must, changes) **Curves are exact** through every operation
  (boolean, offset, fillet, corners, transforms). Curves are split into
  segments only by an output, at that output's tolerance.
- **QP-3** (Must, new) **Each output chooses its own tolerance**: the export
  grid and chord tolerance for GDS/OASIS/DXF and rule checks; the merge
  tolerance and sizes for a mesh; the chord tolerance for drawing.
- **QP-4** (Must, new) Rounding happens **once per output**, never in between:
  nested transforms are combined before they are applied.
- **QP-5** (Must, changes) **Deterministic:** the same project and parameters
  give the same output, byte for byte, on every run (exists) and on every
  supported platform (new: tested across platforms).

### Performance and scale

The numbers are proposals until the open questions are answered.

- **QS-1** (Must) **Interactive rebuild** after a parameter change, measured
  from the change to the redrawn canvas: within **TBD** (proposal: 100 ms)
  for the example resonator, and within **TBD** (proposal: 300 ms) for the
  reference designs in QS-2.
- **QS-2** (Must) **Reference designs** that every change is measured on, and
  that the performance tests build:
  - a perforated plate with 10,000 release holes;
  - a comb drive with 1,000 fingers;
  - the example resonator;
  - TBD: a design typical of real use.
- **QS-3** (Must) **Largest design:** TBD (proposal: a 20 mm × 20 mm die, and
  10⁶ shapes after arrays are expanded).
- **QS-4** (Should) **Exports** of the reference designs to GDS within TBD
  (proposal: 5 s), and opening a project within TBD (proposal: 2 s).
- **QS-5** (Must, exists) An unchanged component is **never rebuilt**; an edit
  rebuilds only what depends on it.
- **QS-6** (Should, new) A component placed or arrayed many times is **built
  once** and instanced, unless its copies differ (MOD-2).
- **QS-7** (Must, new) **Only what depends on a change is rebuilt,** down to
  single shapes, not whole components: the dependencies go through
  expressions, points, alignments, modifiers and `layer_map`. Shapes that do
  not read the changed value, and layers it does not reach, cost nothing.
- **QS-8** (Must, new) **The editor never waits for a build.** Builds run in
  the background; the canvas shows the last finished result and says when a
  newer one is on its way. A newer value cancels a build that is no longer
  needed, inside long operations too.
- **QS-9** (Should, new) **A cheaper result while dragging:** during a drag the
  canvas may show instances without merging them and skip the rule check.
  The full result, rule check included, follows when the drag ends.
- **QS-10** (Could, new) **Speculative builds:** while a value is being
  changed, values it is likely to take next are built ahead in the
  background (for a whole-number parameter such as a tooth count, the
  neighbouring values), so that moving to one of them is a cache hit. They
  never get in the way:
  - **Settings:** off, or how far ahead (e.g. off / 1 / 2 / 4 neighbours
    each way), and the most threads and memory they may use (QS-11).
  - **Only with spare capacity:** they run at the lowest priority and only
    while cores are idle, measured on the machine as a whole, so other
    programs are not slowed. They stop as soon as the load rises.
  - **Only when affordable:** a speculative build is not started when its
    expected cost is above a limit. The cost is estimated from how long the
    same nodes took to build before (recorded with the cache) and from how
    much depends on the value (QS-7). Large rebuilds are left to the real
    change.
  - **Never ahead of real work:** a build for the value actually chosen
    always comes first and cancels speculative ones that are in its way.
  - **Bounded memory:** speculative results are the first to be evicted
    from the cache.
- **QS-11** (Should, new) **Performance settings:** the number of threads
  builds may use, the cache's memory limit, and the speculative builds'
  settings (QS-10), in the editor's settings with sensible defaults from the
  machine (number of cores, memory).

### Robustness

- **QR-1** (Must, exists) An edit never leaves the project unbuildable
  (EDT-3).
- **QR-2** (Must, exists) Every evaluation error names **where** it happened
  (component and shape path) and **why**.
- **QR-3** (Must, new) A geometry operation that fails, or that would exceed
  the precision of QP-1, reports it as an error, never as silently wrong
  geometry.

### Compatibility and installation

- **QC-1** (Must, exists) Every project written by an earlier version opens
  (PRJ-3).
- **QC-2** (Must, exists) The project files do not change because the backend
  changes.
- **QC-3** (Must, exists) Installation is `pip install mems-sketch` (with
  extras for the GUI and MATLAB). No separate compiler or system library is
  needed by users.
- **QC-4** (Must) **Platforms:** TBD (proposal: Linux, Windows and macOS, on
  x86-64 and ARM where wheels are available). Python 3.11 and later.

### Maintainability

- **QM-1** (Must, exists) The backend never imports Qt. A test enforces it.
- **QM-2** (Must, new) The geometry library never depends on the engine. A
  check enforces it.
- **QM-3** (Must, exists) The local checks (lint, format, tests) are the same
  as CI's and stay fast enough to run before every push.
- **QM-4** (Must, exists) Kind-specific behaviour of a shape kind lives in
  that kind's own module; nothing else switches on a kind.

## Constraints

- **C-1** The GUI, project model, storage and CLI are in **Python** (PySide6).
- **C-2** The geometry backend is **C++**: a geometry library on **Open
  CASCADE**, with the mems-sketch engine on top
  ([core architecture](core-architecture.md)).
- **C-3** GDS, OASIS and DXF, and rule checks, use **KLayout**'s library.
- **C-4** **Licences:** a GPL dependency (gmsh) is optional, as an extra or
  a plugin. The rest of the tool does not depend on it.

## Decisions

- **P-1: One fixed internal precision, not a setting.** A precision the user
  could choose would bring nothing:
  - **Finer than 10⁻⁸ µm** (10 femtometres) has no physical meaning. An atom
    is about 0.2 nm across, and mask writers work on grids of 0.25–1 nm.
  - **Coarser** would not be faster with an exact-geometry kernel, and would
    bring back rounding between operations, which is the problem QP-1
    removes.
  - **Different settings would give different geometry** from the same
    project. Results would depend on a setting rather than on the design,
    and every test would have to run under each setting.

  What does vary is chosen **per output** (QP-3): the export grid, which
  depends on the fab; the chord tolerance; the mesh's merge tolerance. The
  editor's snapping grid (EDT-7) is a third, separate setting. For the rare
  case of almost-touching geometry, an operation can use a fuzzy tolerance
  locally, inside the library.

- **X-1: STEP is a Could, not a Must.** Production needs GDS or OASIS, and a
  simulation that takes a mesh from mems-sketch does not need STEP. STEP
  matters only for simulation tools that import CAD geometry and mesh it
  themselves (which many do, to use their own meshing and refinement), and
  for mechanical CAD. Exporting STEP is also slow for large designs: 41 s
  for a 5,000-hole plate in the benchmark. The 3D solids that meshing needs
  (MSH-1) make it cheap to add later. Whether it is needed depends on the
  solvers to support (open question).

- **S-1: No 2D constraint solver.** Open CASCADE does not have one (CAD
  tools that offer "parallel", "tangent", "distance 5 µm" constraints use a
  separate solver). Alignments, points and expressions already cover what
  layouts need: parts placed relative to each other and sizes that follow
  parameters, evaluated in a fixed order rather than solved. If a real need
  shows up, a solver can be added later as its own component.

## Open questions

These set the TBD numbers above.

1. **Designs:** what is a typical design, and the largest? Number of shapes,
   holes or fingers, and die size (QS-2, QS-3).
2. **Interactive budget:** how long may a rebuild take while a value is
   dragged before it feels slow (QS-1)?
3. **Solvers:** which simulation tools must the exports work with? Do they
   take a mesh, or import geometry and mesh it themselves? This decides the
   mesh formats (MSH-5) and whether STEP moves up (X-1).
4. **Platforms** to support (QC-4).
5. **Default export grid and chord tolerance:** are 1 nm and 5 nm right for
   the fabs in use (OUT-2)?
