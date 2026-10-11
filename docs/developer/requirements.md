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

**The work is tracked on GitHub.** Each requirement that is new or changes
what exists has an [issue](https://github.com/TorosBenjamin/mems-sketch/issues?q=label%3Arequirement)
(linked next to its status, labelled by priority and area); this file says
what the tool must do, the issues track doing it. When an issue is closed,
the requirement's status here becomes **exists**.
Answers to the questions that set numbers here are listed under
[Answered questions](#answered-questions).

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

- **PRJ-1** (Must, exists) A project is a **folder of small text files**.
  `project.yaml` is its manifest: name, top component, libraries, the process
  it uses and its overrides, imports, and where its processes and shared
  components are. Each component is a folder with a `component.yaml` (and
  room for whatever else belongs to it); a component lists its private
  components, which are folders inside its own. Each process is a folder with
  a `process.yaml`. Imported files are copied under `imports/`. Names come
  from the manifest and the component files, never from file or folder
  names, so a listed file that is missing is an error and an unlisted one is
  reported.
- **PRJ-2** (Must, exists) The YAML is **canonical**: saving the same project
  twice gives byte-identical files. Values equal to their default are left
  out, keys keep a fixed order, whole numbers have no `.0`, lists of plain
  values are on one line, and unchanged files are not rewritten. Changing one
  value changes one line.
- **PRJ-3** (Must, exists) Files are versioned (`format: mems-sketch/2`).
  Component files are written for reading: parameters, points and shapes
  are maps by name (a parameter with only a default is `name: default`); a
  placement names its component as `ref:` with its parameter values beside
  its other fields. Every shape has a name; a shape made without one gets
  one (`rect1`).
- **PRJ-8** (Must, exists) **Processes are shared like components:** a library
  can hold processes (layers, layer stack, constants and rules) as well as
  components. A project uses one process: its own or one from a library. On
  a library's process, the project may change constants and rules and add
  rules, each change listed with an optional reason, but not change layers or
  the stack, which belong to the fab. When the library's process changes, the
  project follows it except where it changed it.
- **PRJ-4** (Must, exists) A project without a top component is a
  **library** (its manifest is a project's: the same file). A project lists libraries by name and relative path; their
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
- **PRC-2** (Should, new; [#76](https://github.com/TorosBenjamin/mems-sketch/issues/76)) Each layer has a **position in the stack**: its
  bottom z and thickness, as values or expressions over process constants.
  It is used by 3D output ([MSH](#meshing), [VIEW](#3d-view)) and ignored by
  2D output.
- **PRC-3** (Later, new; [#104](https://github.com/TorosBenjamin/mems-sketch/issues/104)) Rules between layers (enclosure, overlap).
- **PRC-4** (Should, new; [#77](https://github.com/TorosBenjamin/mems-sketch/issues/77)) Each layer can have a **material density**, so
  measurements give mass ([MEA-3](#measurements)).
- **PRC-5** (Could, new; [#95](https://github.com/TorosBenjamin/mems-sketch/issues/95)) Each layer can have a **sidewall angle**: vertical
  by default, e.g. 54.74° for KOH etching of (100) silicon. 3D output, cross-
  sections and meshes use it. It describes the shape of the walls only;
  simulating the etch stays out of scope.
- **PRC-6** (Must, exists) **The layer stack is a list of levels**, bottom to
  top (e.g. poly0, poly1, poly2, metal). Each level has its main layer and
  can name **roles** for other layers that belong to it (poly1: `anchor`
  is anchor1, `via` is poly1_poly2_via). One level is the project's
  **default level**. Relative layers ([CMP-10](#components-and-parameters))
  count levels, so adding a layer to a level never moves anything.

### Components and parameters

- **CMP-1** (Must, exists) **Everything is a component.** The design is the
  project's top component, so any project can be placed in another. A
  component has parameters, points and a shape tree. It never stores
  geometry: it is evaluated from its parameters.
- **CMP-2** (Must, exists) **Built-in components** are an ordinary
  component library that ships with the tool: `anchor`, `comb_drive`,
  `serpentine_spring`, with their parameters and points
  (`serpentine_spring`: `start`, `end`; `comb_drive`: `moving`, `fixed`).
  They use relative layers ([CMP-10](#components-and-parameters)), not a
  layer parameter. `rectangle` is retired: `rect` does the same.
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
  **Min and max may be expressions** over the other parameters and process
  constants (`enclosure`: max `size / 2`), and either may be **exclusive**
  (the value must differ from it). The editor shows each limit next to its
  parameter, evaluated.
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
- **CMP-10** (Must, exists) **Every component is on a layer.** A placed
  component's layer is, first match wins: the one set on the placement; the
  component's own default layer, if it declares one; the layer of the
  component that places it. A top component without a default is on the
  default level ([PRC-6](#process)). Inside a component, each shape and each
  placement is on the component's layer (the default), a number of levels
  above or below it (`+1`, `-1`), one of its level's roles (`anchor`), or a
  named layer (`metal`). A relative layer that runs off the bottom or top of
  the stack is an error on that placement, naming the component and layer.
- **CMP-11** (Should, new; [#64](https://github.com/TorosBenjamin/mems-sketch/issues/64)) A component can have **checks** for what one
  parameter's limits cannot say: an expression that must hold and a message
  shown when it does not.
- **CMP-12** (Must, exists) **Libraries use the importing project's stack.** A
  library has no layers of its own; its components normally use only
  relative layers and roles, so they work in any project with enough levels.
  A library made for one process may name layers, and then says which
  process; a named layer the project lacks is an error naming the library
  and the layer.

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
  flush, square or round ends). Each is on one layer
  ([CMP-10](#components-and-parameters)).
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
- **SHP-8** (Must, exists; [#63](https://github.com/TorosBenjamin/mems-sketch/issues/63)) Circles, arcs, round path ends, fillets, rounded
  corners and the round joins of offsets are **exact curves**, not polygons
  ([QP-2](#precision)), unless a shape asks for a number of `segments`.
- **SHP-9** (Should, new; [#84](https://github.com/TorosBenjamin/mems-sketch/issues/84)) **Paths along curves:** a path's centreline can
  have arc segments (a given radius, or tangent to the previous segment) as
  well as straight ones, with an exact width. Folded springs get true round
  turns.
- **SHP-10** (Should, new; [#80](https://github.com/TorosBenjamin/mems-sketch/issues/80)) **Variable width:** a path's width can change
  along it (linearly, or as an expression of the position along it), for
  tapered beams, tapered comb fingers and stress-relief shapes at flexure
  roots.
- **SHP-11** (Could, new; [#97](https://github.com/TorosBenjamin/mems-sketch/issues/97)) **Splines:** a polygon or path edge can be a smooth
  curve through given points (or with control points), for free-form
  outlines such as optimised spring profiles.
- **SHP-12** (Should, new; [#81](https://github.com/TorosBenjamin/mems-sketch/issues/81)) **One polygon, with curved segments:** a polygon's
  boundary is a list of segments, each straight or an arc (given by a
  radius, a bulge, or tangent to the previous segment), and later a spline
  (SHP-11). Any outline can then be drawn exactly: rounded slots, curved
  electrodes, cam profiles. The other primitives (rect, circle, arc, path,
  text) are made by factories on the same construction ([decision
  G-1](#decisions)).
- **SHP-13** (Should, new; [#82](https://github.com/TorosBenjamin/mems-sketch/issues/82)) **Text** as geometry: letters as shapes on a layer,
  fabricated like any other shape (die names, version numbers, orientation
  marks, labels next to test structures). Height, alignment, and for a
  stroke font the stroke width, are values and expressions. Text goes
  through booleans, the rule check and every export like other shapes.
  - **Stroke fonts** (letters as centrelines, e.g. the Hershey fonts) are
    thickened like a path, so the stroke width can be set at or above the
    layer's minimum width: the default for fabricated text.
  - **Outline fonts** (TrueType, OpenType) are read as exact outlines (lines
    and curves), never rasterized.
  - **Fonts ship with mems-sketch** (a stroke font and an open sans-serif
    outline font), and a project can keep its own font file as it keeps
    imported layouts, so the same project gives the same text on every
    machine (QP-5). System fonts are not used unless copied into the
    project.
- **SHP-14** (Should, new; [#83](https://github.com/TorosBenjamin/mems-sketch/issues/83)) **Perforation:** fill a region with holes (any
  shape) on a square or hexagonal grid, keeping a margin from the region's
  edges and from keep-out shapes (anchors, other layers mapped in). The holes
  follow when the region changes. For release holes.
- **SHP-15** (Could, new; [#98](https://github.com/TorosBenjamin/mems-sketch/issues/98)) More factory shapes: **regular polygon** (n sides),
  **ellipse** (exact), **spiral** (Archimedean, with a width).

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
- **PNT-5** (Could, new; [#94](https://github.com/TorosBenjamin/mems-sketch/issues/94)) **Points of the exact geometry** as well as of the
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
- **MEA-2** (Should, new; [#67](https://github.com/TorosBenjamin/mems-sketch/issues/67)) The Measure tool also measures **between shapes**:
  the exact minimum distance between two shapes (or two edges) on a layer,
  the length of an edge, and the radius and centre of an arc. A measurement
  can be kept on the canvas as a ruler that follows the design when it
  changes.
- **MEA-3** (Should, new; [#68](https://github.com/TorosBenjamin/mems-sketch/issues/68)) An **information panel** shows the properties of the
  selection, per layer: area, perimeter, bounding box, centroid and second
  moments of area; with the layers' thickness (PRC-2) and density (PRC-4),
  also volume, mass and moments of inertia. For several selected shapes it
  shows each and their total.
- **MEA-4** (Should, new; [#69](https://github.com/TorosBenjamin/mems-sketch/issues/69)) **Measurements in expressions:** a shape's
  measurements can be used like its points, e.g. `mass.area`,
  `mass.centroid.x`, `mass.mass`, `mass.inertia_z`, and functions such as
  `gap(finger, stator)` (minimum distance) and `overlap(rotor, stator)`
  (length or area of overlap). A design can then be driven by them: a
  counterweight sized so that the centroid sits on the pivot. Dependency
  order and loop errors apply as for other values (EXP-4).
- **MEA-5** (Could, new; [#91](https://github.com/TorosBenjamin/mems-sketch/issues/91)) Measurements are available from the command line and
  the Python API (`mems-sketch-cli info --measure`), for sweeps and reports.

### Cross-sections

- **XS-1** (Could, new; [#100](https://github.com/TorosBenjamin/mems-sketch/issues/100)) **A cross-section** of the layer stack along a line
  drawn on the canvas: the exact profile of each layer (with its thickness,
  z position and sidewall angle), shown in a view of its own and updated as
  the design changes.
- **XS-2** (Could, new; [#101](https://github.com/TorosBenjamin/mems-sketch/issues/101)) A cross-section line is saved with the component and
  can be aligned like a guide, so it stays where it matters (e.g. through a
  comb's fingers) when the design changes.
- **XS-3** (Could, new; [#102](https://github.com/TorosBenjamin/mems-sketch/issues/102)) Cross-sections can be exported as a drawing (SVG,
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
- **EDT-7** (Must, changes; [#48](https://github.com/TorosBenjamin/mems-sketch/issues/48)) **Snapping in the editor** (to points and to a
  grid) is a convenience of the editor, set by the user. It is separate
  from the geometry's precision and from an export's grid.
- **EDT-8** (Should, new; [#66](https://github.com/TorosBenjamin/mems-sketch/issues/66)) **What a value affects:** selecting or hovering a
  parameter (or a process constant, or a shape's value) highlights everything
  that depends on it: shapes, points, alignments and other parameters,
  directly or through other values. It comes from the dependency graph
  (QS-7), so it is shown without building anything.
- **EDT-9** (Could, new; [#88](https://github.com/TorosBenjamin/mems-sketch/issues/88)) **The change a value makes:** while a value is
  dragged or tried, the canvas can show the geometric difference from where
  it started (material added and removed, as in the History panel, HIS-2).
- **EDT-10** (Could, new; [#87](https://github.com/TorosBenjamin/mems-sketch/issues/87)) **Annotations:** notes on the canvas, saved with
  the component and alignable like guides, never fabricated. They can
  optionally be exported as GDS text labels (zero-area text records) on a
  chosen layer, which other tools read as names.

### Rule checks

- **DRC-1** (Must, exists) **Rules are project data:** a list of rules,
  each naming a rule kind, the layers it applies to, its values, a severity
  (error or warning) and an optional message. Minimum width and spacing per
  layer, set in the process today, become rules of the built-in kinds;
  existing projects open with them as such rules.
- **DRC-2** (Must, exists) The check runs on the final geometry **after every
  change**. Violations are listed and marked on the canvas, and each can be
  clicked to go there.
- **DRC-3** (Must, exists; [#47](https://github.com/TorosBenjamin/mems-sketch/issues/47)) The check runs on the geometry **as it will be
  exported** (snapped to the export grid, curves split at the export's chord
  tolerance), because that is what the fab checks.
- **DRC-4** (Must, exists) From the command line, `check` exits with status 1
  on violations and 2 on errors, so it can gate CI.
- **DRC-5** (Could, new; [#86](https://github.com/TorosBenjamin/mems-sketch/issues/86)) **Pattern density** per layer: the fraction of area
  covered, overall and as a map over a grid, with optional minimum and
  maximum limits as a rule. Etch rates (e.g. in DRIE) depend on it.
- **DRC-6** (Must, exists) **Rules are parametric:** every value in a rule is an
  expression over the process constants and the parameters of its rule set
  (DRC-8), evaluated like any other expression (EXP). Changing a constant
  such as `undercut` re-checks every rule that reads it.
- **DRC-7** (Must, exists) **Default rules:** a new project starts with a
  default rule set: minimum width and spacing per layer, `anchored` and
  `release` (DRC-11). Every rule and value can be changed, and any rule can
  be turned off. A rule turned off stays listed as off, so a check never
  passes because a rule silently went away.
- **DRC-8** (Should, exists) **Shared rules come with the process**
  ([PRJ-8](#projects-and-files)): a fab's rules and the constants they use
  are shared as a process in a library. A project using it can change a
  constant (a different `undercut` for a different etch), change or turn off
  one of its rules and add rules of its own, each with an optional reason.
  There are no separate rule decks.
- **DRC-9** (Must, exists) **Rule kinds are plugins**, like exporters (OUT-7): a
  kind declares its parameters (as exporters declare their options, OUT-8)
  and checks the geometry as exported (DRC-3), returning violations with
  their locations. Kinds are found through the `mems_sketch.rules`
  entry-point group, so a team can install its own; the built-in kinds are
  plugins too. The rules editor and the command line are built from the
  declarations.
- **DRC-10** (Must, exists) **No code in project files:** projects and processes
  only name rule kinds and give them values. A rule whose kind is not
  installed is reported as not checked, an error, and never passes.
- **DRC-11** (Should, new; [#65](https://github.com/TorosBenjamin/mems-sketch/issues/65)) **Built-in rule kinds:**
  - per layer: minimum width, minimum spacing, minimum area, minimum hole
    area, maximum width, minimum angle (no sharp spikes), density (DRC-5);
  - between layers: enclosure, separation, overlap, inside, not
    overlapping;
  - MEMS topology:
    - `anchored`: every piece of a structural layer touches an anchor, so
      nothing floats away at release;
    - `release`: released parts are narrow enough for the etch to undercut
      them (at most twice the undercut wide), and anchors are wide enough to
      survive it, which catches a plate missing its release holes;
    - `connected`: a layer is one piece, or a given number of pieces.
- **DRC-12** (Must, exists) A violation names its **rule, layers, location** and
  the values it was checked with. Errors make `check` exit with status 1
  (DRC-4); warnings are listed and do so only with `--strict`.
- **DRC-13** (Could, exists) **Waivers:** a single violation can be accepted
  with a reason, saved with the component and listed in the check's
  results. A waiver lapses when the geometry it covers changes.

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
- **IMP-5** (Could, new; [#89](https://github.com/TorosBenjamin/mems-sketch/issues/89)) **DXF with true curves:** arcs and circles in a DXF
  file are imported as exact curves, not polygons.
- **IMP-6** (Later, new; [#103](https://github.com/TorosBenjamin/mems-sketch/issues/103)) **STEP and IGES import** of 3D parts (a package, a
  mechanical part) to check how a design fits with them, in the 3D view.
- **IMP-7** (Could, new; [#90](https://github.com/TorosBenjamin/mems-sketch/issues/90)) Imported geometry that is slightly broken (tiny
  gaps, self-touching outlines) is **repaired** where that is unambiguous, and
  what was repaired is reported.

### Export

- **OUT-1** (Must, exists) Export a component, with its current trial
  values, to **GDSII, OASIS and DXF**, with the layers' GDS numbers.
- **OUT-2** (Must, exists; [#53](https://github.com/TorosBenjamin/mems-sketch/issues/53)) Each export to a grid **chooses its grid**
  (default 1 nm) and **chord tolerance** for curves (default 5 nm).
  Neither changes the design.
- **OUT-3** (Must, exists; [#54](https://github.com/TorosBenjamin/mems-sketch/issues/54)) An export to a grid **reports what snapping
  changed** in the shape of the geometry, with where: pieces that vanished,
  split or merged (a neck or gap narrower than the grid), and holes that
  closed, joined another or the outside, or formed (a notch's mouth
  closing). It also gives the area before and after. Every edge moves by up
  to half a grid step, so a width can change by up to one step anywhere off
  the grid; that is what a grid means, and is not reported edge by edge.
- **OUT-4** (Should, new; [#74](https://github.com/TorosBenjamin/mems-sketch/issues/74)) GDS and OASIS exports **keep the hierarchy**:
  components as cells, arrays as array references.
- **OUT-5** (Must, exists) Export a component's **geometry as data** (JSON,
  XML, `.mat`): polygons per layer with their holes, in µm, plus the
  component's points and parameter values.
- **OUT-6** (Should, new; [#75](https://github.com/TorosBenjamin/mems-sketch/issues/75)) Export **STEP**: the layers extruded through the
  stack into solids, with exact curves. For simulation tools that import CAD
  and mesh it themselves, and for mechanical CAD (packaging, assemblies).
  See [decision X-1](#decisions).
- **OUT-7** (Must, exists) **Export formats are plugins:** a new format can
  be added in its own package, in Python, without changing or rebuilding
  mems-sketch. The built-in formats are plugins too. See
  [decision E-1](#decisions).
- **OUT-8** (Should, exists) Each exporter **declares its options** (grid,
  chord tolerance, hierarchy, its own settings); the export dialog and the
  command line are built from the declaration, with no code per format.

### Meshing

Most users have no licence for a commercial mesher or solver, so a mesh
from mems-sketch is their way into simulation; those who have one (e.g.
Ansys) can mesh the STEP export themselves (OUT-6).

- **MSH-1** (Must, new; [#49](https://github.com/TorosBenjamin/mems-sketch/issues/49)) Export a **mesh** of a component for simulation:
  2D (the layers' faces) or 3D (the layers extruded through the stack).
- **MSH-2** (Must, new; [#50](https://github.com/TorosBenjamin/mems-sketch/issues/50)) **Mesh settings are project data**, as expressions:
  a global size, sizes per layer, and refinement regions (areas, points,
  edges, distances from edges) drawn on the canvas. They are saved, diffed
  and parametric like everything else.
- **MSH-3** (Should, new; [#70](https://github.com/TorosBenjamin/mems-sketch/issues/70)) The mesh is generated from the **exact geometry**,
  not the export grid, with its own merge tolerance (e.g. 10⁻⁶ µm).
- **MSH-4** (Should, new; [#71](https://github.com/TorosBenjamin/mems-sketch/issues/71)) Mesh regions and boundaries are **named** after
  layers and named shapes (physical groups), so a solver finds anchors and
  electrodes by name.
- **MSH-5** (Must, new; [#51](https://github.com/TorosBenjamin/mems-sketch/issues/51)) Mesh formats for **free solvers** first: Elmer
  (good MEMS support: electrostatics, structures, modes), CalculiX (Abaqus
  `.inp`), and FEniCS or scikit-fem (through meshio, e.g. XDMF); gmsh's
  `.msh` itself. Abaqus `.inp` and Nastran `.bdf` also go into commercial
  tools such as Ansys Mechanical. Further formats are exporter plugins
  (OUT-7).
- **MSH-6** (Should, new; [#72](https://github.com/TorosBenjamin/mems-sketch/issues/72)) A **preview** of the mesh on the canvas, with element
  count and quality.
- **MSH-7** (Must, new; [#52](https://github.com/TorosBenjamin/mems-sketch/issues/52)) **Graded sizes, no seams:** where regions of
  different sizes meet (a finely meshed spring on a coarse frame), the
  element size changes gradually, by at most a growth rate per element
  (a project setting, e.g. 1.2), never in one step. The mesh is conforming
  everywhere: neighbouring regions share their nodes. A sudden jump in size
  makes distorted elements and unreliable results along the seam.
- **MSH-8** (Should, new; [#73](https://github.com/TorosBenjamin/mems-sketch/issues/73)) An **automatic size map** from the geometry:
  smaller elements in narrow beams, small gaps (between comb fingers),
  tight curves and sharp corners. It is what gmsh's own sizing makes
  (curvature, distance between edges), shown on the canvas as a colour
  scale so the user sees where the mesh will be fine before meshing.
- **MSH-9** (Could, new; [#93](https://github.com/TorosBenjamin/mems-sketch/issues/93)) **Editing the size map with a brush,** where the
  automatic map does not know enough, such as the stress at a spring's
  root. Only the edits are saved, as project data, not the whole map: when
  the design changes, the automatic map follows the new geometry and the
  edits stay where they were painted. Grading (MSH-7) applies on top, so a
  rough painting cannot make a seam.
- **MSH-10** (Could, new; [#92](https://github.com/TorosBenjamin/mems-sketch/issues/92)) **Refinement from a solution:** solve on a
  coarse mesh with a free solver, estimate the error, refine where it is
  large and mesh again. It needs a solver in the loop, so it comes after
  the rest.

### 3D view

- **VIEW-1** (Could, new; [#99](https://github.com/TorosBenjamin/mems-sketch/issues/99)) A 3D view of a component: the layers extruded
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
- **CLI-5** (Could, new; [#85](https://github.com/TorosBenjamin/mems-sketch/issues/85)) The geometry library usable **on its own** from
  Python and C++, without projects
  ([core architecture](core-architecture.md#overview)).

## Quality requirements

### Precision

- **QP-1** (Must, exists; [#55](https://github.com/TorosBenjamin/mems-sketch/issues/55)) The geometry has **one internal precision of
  10⁻⁸ µm or finer**, everywhere, for every design size the tool supports
  ([QS-3](#performance-and-scale)). It is fixed, not a setting
  ([decision P-1](#decisions)): the geometry library works in doubles, in
  nanometres, with Open CASCADE's tolerance of 10⁻⁷ nm.
- **QP-2** (Must, exists; [#56](https://github.com/TorosBenjamin/mems-sketch/issues/56)) **Curves are exact** through every operation
  (boolean, offset, fillet, corners, transforms). Curves are split into
  segments only by an output, at that output's tolerance.
- **QP-3** (Must, exists; [#57](https://github.com/TorosBenjamin/mems-sketch/issues/57)) **Each output chooses its own tolerance**: the export
  grid and chord tolerance for GDS/OASIS/DXF and rule checks; the merge
  tolerance and sizes for a mesh; the chord tolerance for drawing.
- **QP-4** (Must, exists; [#58](https://github.com/TorosBenjamin/mems-sketch/issues/58)) Rounding happens **once per output**, never in between:
  nested transforms are combined before they are applied.
- **QP-5** (Must, exists; [#59](https://github.com/TorosBenjamin/mems-sketch/issues/59)) **Deterministic:** the same project and parameters
  give the same output, byte for byte, on every run and on every supported
  platform (`tests/test_deterministic.py`, run by the wheels workflow on Linux,
  Windows and macOS). GDSII files carry a fixed date, or `SOURCE_DATE_EPOCH`'s.

### Performance and scale

Real designs are a few millimetres across at most, with up to a few hundred
holes, though every kind of sensor has its own sizes and problems (see
[Answered questions](#answered-questions)). The reference designs are about
ten times larger than that, so the numbers hold with room to spare.

- **QS-1** (Must) **Interactive rebuild:** dragging stays smooth, and the
  correct result may follow a moment later.
  - The canvas follows the mouse at its frame rate (the "Frame rate limit"
    setting) and never waits for a build (QS-8).
  - The full, correct result, rule check included, is drawn within 100 ms
    of a change for the example resonator, and within 1 s for the reference
    designs (QS-2).
  - While a build runs, the canvas shows the last finished result, or a
    cheaper one (QS-9).
- **QS-2** (Must) **Reference designs** that every change is measured on, and
  that the performance tests build:
  - a perforated plate with 10,000 release holes;
  - a comb drive with 1,000 fingers;
  - the example resonator;
  - a plate of a few millimetres with a few hundred holes, as a typical
    design; replaced by a real one when a design team provides it.
- **QS-3** (Must) **Largest design:** a 10 mm × 10 mm die and 10⁵ shapes
  after arrays are expanded (real designs: a few millimetres, a few hundred
  holes).
- **QS-4** (Should) **Exports** of the reference designs to GDS within 5 s,
  and opening a project within 2 s.
- **QS-5** (Must, exists) An unchanged component is **never rebuilt**; an edit
  rebuilds only what depends on it.
- **QS-6** (Should, exists) A component placed or arrayed many times is **built
  once** and instanced, unless its copies differ (MOD-2).
- **QS-7** (Must, new; [#61](https://github.com/TorosBenjamin/mems-sketch/issues/61)) **Only what depends on a change is rebuilt,** down to
  single shapes, not whole components: the dependencies go through
  expressions, points, alignments, modifiers and `layer_map`. Shapes that do
  not read the changed value, and layers it does not reach, cost nothing.
- **QS-8** (Must, new; [#62](https://github.com/TorosBenjamin/mems-sketch/issues/62)) **The editor never waits for a build.** Builds run in
  the background; the canvas shows the last finished result and says when a
  newer one is on its way. A newer value cancels a build that is no longer
  needed, inside long operations too.
- **QS-9** (Should, new; [#79](https://github.com/TorosBenjamin/mems-sketch/issues/79)) **A cheaper result while dragging:** during a drag the
  canvas may show instances without merging them and skip the rule check.
  The full result, rule check included, follows when the drag ends. A
  setting turns it off, next to "Draft quality while zooming and resizing"
  (which only drops smoothing when drawing, not accuracy).
- **QS-10** (Could, new; [#96](https://github.com/TorosBenjamin/mems-sketch/issues/96)) **Speculative builds:** while a value is being
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
- **QS-11** (Should, new; [#78](https://github.com/TorosBenjamin/mems-sketch/issues/78)) **Performance settings:** the number of threads
  builds may use, the cache's memory limit, and the speculative builds'
  settings (QS-10), in the editor's settings with sensible defaults from the
  machine (number of cores, memory).

### Robustness

- **QR-1** (Must, exists) An edit never leaves the project unbuildable
  (EDT-3).
- **QR-2** (Must, exists) Every evaluation error names **where** it happened
  (component and shape path) and **why**.
- **QR-3** (Must, new; [#60](https://github.com/TorosBenjamin/mems-sketch/issues/60)) A geometry operation that fails, or that would exceed
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
- **QC-4** (Must) **Platforms:** Linux and Windows on x86-64, Python 3.11
  and later. macOS (ARM) is a Should: its wheels are built in the same
  release job, and a release does not wait for them if they fail.

### Maintainability

- **QM-1** (Must, exists) The backend never imports Qt. A test enforces it.
- **QM-2** (Must, exists) The geometry library never depends on the engine. A
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
- **C-3** GDS, OASIS and DXF are read and written in plain Python
  (`mems_sketch.layout`); rule checks use the geometry library's grid
  booleans and offsets (Clipper2). No layout library is a dependency.
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

- **X-1: STEP is a Should, not a Must.** Production needs GDS or OASIS, and
  a simulation that takes a mesh from mems-sketch does not need STEP. But
  commercial tools such as Ansys import CAD geometry and mesh it
  themselves, with meshers better than any we could offer, so STEP is the
  most direct way into them. It was a Could while
  STEP export looked slow (41 s for a 5,000-hole plate in the benchmark);
  real designs have a few hundred holes, where it should take seconds at
  most (it grew faster than the number of holes in the benchmark). The
  3D solids that meshing needs (MSH-1) are the same ones STEP writes.

- **S-1: No 2D constraint solver.** Open CASCADE does not have one (CAD
  tools that offer "parallel", "tangent", "distance 5 µm" constraints use a
  separate solver). Alignments, points and expressions already cover what
  layouts need: parts placed relative to each other and sizes that follow
  parameters, evaluated in a fixed order rather than solved. If a real need
  shows up, a solver can be added later as its own component.

- **G-1: One construction for every shape, factories for the user's
  shapes.** Every shape is a face bounded by line, arc and spline segments;
  the library has one constructor for it, and every other shape is a
  factory on top. Geometry goes in the library, meaning in the engine:
  - **The geometry library** has the constructor, the factories that are
    pure geometry (circle, arc, ellipse, regular polygon, path outline from
    a centreline and a width, spiral, text outlines from a font, perforation)
    and the operations. Numbers in, regions out; no names, expressions or
    files.
  - **The engine** has the shape kinds, the user's vocabulary: a kind reads
    its node from the project, evaluates its expressions, calls a factory,
    names its points (`center`, `start`) and says which values the canvas
    can drag. It decides which kinds exist and what is in the YAML.
  - **The frontend** presents them: the Add menu, drawing tools, forms and
    handles.
  - **Device-level parts** (combs, springs, alignment marks, test
    structures) are components, built-in or in libraries, made of kinds as a
    user would make them.

- **E-1: File formats live outside the C++ backend.** Exporters are Python
  plugins (OUT-7). The geometry library provides the building blocks every
  exporter needs: the cell hierarchy, outlines at a chord tolerance,
  outlines snapped to a grid with the snapping report (OUT-3), solids and
  triangles, and OCC's own BREP and STEP writers. Snapping is done once, in
  the library, so every grid-based format reports the same changes. Adding
  a format then needs no C++.

- **R-1: Rules are data; rule kinds are code in plugins.** A project or a
  process only names rule kinds and gives them values (DRC-10). Allowing code in
  project files (`check: "region.area() > 5"`) would mean that opening
  someone's project runs their code, and rules would stop being plain data
  that diffs and merges like the rest of the project. New kinds of check are
  written once, as a plugin with declared parameters (DRC-9), and then used
  in any project like the built-in ones.

## Answered questions

1. **Designs** (QS-2, QS-3): the largest are a few millimetres across, with
   up to a few hundred holes. Sensors differ a lot in size and problems, so
   the reference designs stay about ten times larger than that.
2. **Interactive budget** (QS-1): dragging should feel smooth, but the
   correct result may take a moment to follow.
3. **Solvers** (MSH-5, X-1): mems-sketch is an open project for anyone
   interested, and most of them have no licence for commercial tools.
   Meshing is therefore its own feature, with formats for free solvers
   first. People with Ansys, whose mesher is hard to beat, mesh the STEP
   export there.
4. **Default export grid and chord tolerance** (OUT-2): 1 nm and 5 nm. Each
   export can change them, so the defaults only need to be sensible.
5. **Layers of components** (CMP-10, CMP-12, PRC-6): a component is on a
   layer, which a placement can always change, and its shapes sit relative
   to it. This replaces a layer map per library: the same library comb can
   be placed on poly1 in one place and poly2 in another. Relative layers
   count levels of the stack rather than entries in a list of layers, so a
   via or dimple layer between two structural layers does not shift them.
6. **Limits** (CMP-5, CMP-11): min and max stay, because the editor shows
   them where the value is edited, but they may be expressions and
   exclusive; component checks cover the rest.
7. **Platforms** (QC-4): Windows and Linux; macOS if it costs little, which
   it does (the same build on another CI runner).
