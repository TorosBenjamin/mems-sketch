# Geometry core architecture

> **Status: target architecture, not built yet.** It describes the geometry
> backend mems-sketch is moving to. Until a part is migrated, the code works
> as [Architecture](architecture.md) describes. Each migration step below
> updates both documents.

The requirements it has to meet are in [Requirements](requirements.md).

mems-sketch is moving its geometry backend into a C++ core built on
[Open CASCADE Technology](https://dev.opencascade.org/) (OCC). The Qt editor,
the project model, files, history and the CLI stay in Python.

## Why

The current backend builds everything as KLayout regions on a 1 nm integer
grid. That is right for masks, and wrong for the work around them:

- **One grid for everything.** Every shape is snapped to 1 nm as soon as it
  is built. Booleans, offsets and fillets all run on that grid, so their
  errors build up, and an exported mesh inherits 1 nm slivers and rounded
  angles. KLayout's boolean engine uses 32-bit integers, so a finer grid
  shrinks the largest possible design: ±21 µm at 10⁻⁸ µm.
- **No true curves.** Circles, arcs and fillets become polygons (5 nm chord
  deviation) at build time. A mesher then sees fake corners, exactly where
  stress matters (flexure roots, fillets).
- **2D only.** No solids, no STEP, nothing for a mesher or a 3D view.
- **Copies instead of instances.** Placements and arrays copy every polygon,
  and an array evaluates its node again for every copy.

## Goals

1. **One geometry tolerance**, 10⁻⁸ µm or finer, with exact lines and arcs.
   Exports choose their own: GDS snaps to its grid (1 nm), a mesher uses its
   merge tolerance (e.g. 10⁻⁶ µm), and curves are split into segments only
   for an output that needs it, to that output's chord tolerance.
2. **3D from the process stack.** Layers extruded by their thickness into
   solids, for meshing (gmsh) and a 3D view, and optionally STEP.
3. **Interactive speed on MEMS-sized designs**, such as plates with 10,000
   release holes and combs with 1,000 fingers, while a parameter is dragged.
4. **The same projects.** The YAML files, expressions, shape kinds,
   modifiers, alignments and results stay as they are. A project that builds
   today builds the same, within 1 nm, on the new core.

Out of scope: simulation itself, and editing a mesh by hand. Meshes are
controlled by their settings (sizes and refinement regions), which stay
parametric.

## Overview

The C++ side is **two layers**: a general geometry library, and the
mems-sketch engine built on it.

```
 Python                     │  C++
                            │
 GUI · CLI · scripts        │  engine  (mems_sketch._core)
 project model (pydantic) ──┼─►  expressions, shape tree, modifiers,
 storage, git, history      │    alignments, compiler and cache
       ▲                    │       │ uses only the library's public API
       │                    │       ▼
       │                    │  geometry library: mgeom  (mems_sketch._geom)
       │                    │    layers of exact 2D faces, cells and
       └── results ◄────────┼──  instances, booleans with fast paths,
                            │    offsets, fillets, layer stack, outputs
                            │       │
                            │       ▼
                            │  Open CASCADE (OCC) · gmsh (optional)
```

File formats are not part of either C++ layer. They are **exporter plugins in
Python** (see [Export modules](#export-modules)), built on what the library
provides.

| Layer | Knows about | Never knows about |
|---|---|---|
| **Geometry library** (`mgeom`, working name) | Layers, faces with exact lines and arcs, cells, instances and arrays, rigid transforms, a layer stack, outlines, snapping, solids | Projects, YAML, parameters, expressions, shape kinds, alignment points, file formats other than OCC's own |
| **Engine** | The project model, expressions, shape kinds, modifiers, alignments, fingerprints, what to rebuild | OCC: it only calls the library |
| **Python** | The GUI, files, git, editing, undo/redo | OCC and the library's internals: it sees plain data |

**The library never depends on the engine.** It is a separate CMake target
with its own headers, namespace and tests. A check fails the build if it
includes an engine header, the way `tests/test_architecture.py` keeps Qt out
of the backend today. That keeps mems-sketch specifics out of the geometry,
lets each layer be tested alone, and lets the kernel change (see
[Measurements](#measurements)) without touching the engine.

**Status of the library: a library in structure, internal for now.** It is
built and tested as a library, and Python scripts can use it directly through
`mems_sketch._geom`. But its API is not promised to anyone outside
mems-sketch until the migration is done and the API has stopped moving. After
that it can be released as its own package. As far as we know no open
library combines 2D layered layout geometry, exact curves, fast arrays, and
both GDS and STEP output. KLayout and gdsfactory work on polygons on a grid,
and CadQuery and build123d are 3D CAD.

### The Python boundary

The boundary with Python is at the **component**, not at the operation.
Python hands the engine a whole validated project. The engine evaluates it,
and Python asks for results. A rebuild after an edit is one call, not one
call per shape, so a 10,000-cell array never makes 10,000 calls across the
boundary.

| Stays in Python | Moves to C++ |
|---|---|
| The Qt GUI: canvas, tools, panels, History panel | Engine: expression evaluation (`core/expressions.py`) |
| The project model: pydantic classes are still the one definition of the file format | Engine: shape kinds, modifiers, alignments and points (`core/shapes/`) |
| Storage: project folders, documents, git | Engine: the compiler, fingerprints, cache, evaluation order (`core/compiler.py`) |
| `EditSession`: commands, transactions, undo/redo | Library: geometry, booleans, offsets, fillets, extrusion |
| History diffs in words (`core/diff.py`) | Library: outlines at a chord tolerance, snapping to a grid with its report |
| The CLI and the Python scripting API | Library: solids, triangles, and OCC's own writers (BREP, STEP) |
| All exporter plugins, GDS/OASIS/DXF included, and rule checks (KLayout's Python package, on the snapped outlines) | Library: meshing (gmsh, optional) |
| Reading GDS/OASIS cells for imported layouts (KLayout's Python package) | |

Neither C++ layer imports Python or Qt. Like the Python backend today, both
can be tested and used without a GUI.

## The geometry library

Every shape is a face bounded by line, arc and spline segments, made by one
constructor; the shapes users see are factories on top of it, and the shape
kinds that name them live in the engine (requirements decision G-1).

### API

Public API lengths are in µm, as doubles. The library works in nm inside (see
[Kernel and precision](#kernel-and-precision)). Every object is immutable and
cheap to copy: copies share their geometry.

```cpp
namespace mgeom {

// A rigid transform: mirror, then rotate, then move. Composes with *.
struct Transform { double dx, dy, angle_deg; bool mirror_x; };

// Faces on one layer: exact lines and arcs, holes allowed.
class Region {
 public:
  static Region rect(double x0, double y0, double x1, double y1);
  static Region polygon(std::span<const Point> points);
  static Region circle(Point centre, double r);
  static Region arc(Point centre, double r_in, double r_out, double from_deg, double to_deg);
  static Region path(const Wire& centreline, const Width& width, PathEnds ends);
  static Region polygon(const Wire& outline);  // lines, arcs and splines: the one constructor
  static Region text(std::string_view text, const Font& font, const TextStyle& style);
  static Region perforate(const Region& area, const Region& hole, const Grid& grid,
                          double margin, const Region& keep_out);

  Region operator|(const Region&) const;  // union
  Region operator-(const Region&) const;  // subtract
  Region operator&(const Region&) const;  // intersect
  Region operator^(const Region&) const;  // xor
  Region offset(double distance, Join join) const;
  Region rounded(std::span<const CornerRounding> corners) const;  // per-corner fillet or chamfer
  Region transformed(const Transform&) const;                    // shares geometry
  Box bbox() const;
  std::vector<Corner> corners() const;  // vertices where edges meet at an angle
  Properties properties() const;        // area, perimeter, centroid, second moments
  std::vector<Point> points(PointKind) const;  // arc centres, edge midpoints, ...
};

// A centreline or outline: straight, arc and spline segments.
class Wire {
 public:
  explicit Wire(Point start);
  Wire& line_to(Point p);
  Wire& arc_to(Point p, double radius);      // or tangent to the previous segment
  Wire& spline_through(std::span<const Point> points);
};

// A reusable piece of layout: regions per layer plus placed cells.
class Cell {
 public:
  void add(LayerId, Region);
  void place(CellRef, Transform);                         // one instance
  void place_array(CellRef, Transform, ArraySpec);        // columns x rows, steps
  void place_polar(CellRef, Transform, PolarSpec);        // count, centre, step, rotate
  Region flat(LayerId) const;                             // instances resolved, merged
};

class Layout {  // cells, layers and a process stack
 public:
  CellRef cell(std::string name);
  void set_stack(LayerStack);   // per layer: z and thickness
};

// Outputs: none of them changes the layout. File formats are exporter
// plugins built on these (see Export modules).
Outlines outlines(const Cell&, LayerId, double chord);          // rings for drawing and exporters
Snapped snapped(const Cell&, LayerId, Grid grid, double chord); // integer rings + SnapReport
Triangles triangles(const Cell&, LayerId, double chord);        // 2D or extruded
void write_step(const Layout&, CellRef top, const path&);       // OCC's own writers
void write_brep(const Layout&, CellRef top, const path&);
Mesh mesh(const Layout&, CellRef top, const MeshSettings&);     // gmsh, optional

// Measurements and sections (requirements MEA, XS, DRC-5).
Distance distance(const Region&, const Region&);   // exact minimum, with the two closest points
double overlap_length(const Region&, const Region&);
std::vector<Profile> section(const Layout&, CellRef top, Point from, Point to);  // through the stack
DensityMap density(const Region&, Box area, double cell);

}  // namespace mgeom
```

The same API is bound to Python as `mems_sketch._geom`, with NumPy arrays for
points, outlines and triangles. This is a sketch: names and details settle
during the migration.

### Kernel and precision

- **Unit inside: the nanometre.** OCC treats points within
  `Precision::Confusion()` = 10⁻⁷ *model units* as the same. In nanometres
  that is 10⁻¹⁰ µm, below the 10⁻⁸ µm goal, and a die of several centimetres
  is still well inside double precision.
- **Exact curves.** Circles, arcs, fillets and the round joins of offsets are
  OCC circles, not polygons. Curves are split into segments only by an
  output, at that output's chord tolerance.
- **A region is planar faces.** OCC's booleans, offsets
  (`BRepOffsetAPI_MakeOffset`) and 2D fillets (`BRepFilletAPI_MakeFillet2d`)
  work on faces in the plane z = 0.
- **Tolerance growth is checked.** OCC can loosen the tolerance of edges and
  vertices after many operations. Tests assert that the tolerance of results
  stays below the 10⁻⁸ µm goal, and an operation that exceeds it says so.
- **Text from fonts, not rasterized.** Outline fonts are read with FreeType:
  each letter's lines and Bézier curves become the segments of the one
  constructor, so letters are exact. Stroke fonts (Hershey) give
  centrelines, thickened by the path factory. Fonts ship with the library;
  OCC's own text builder is not used, as it would bring in the
  visualization module.
- **Only the OCC modules needed**, linked statically: Foundation, Modeling
  Data, Modeling Algorithms, and the STEP part of Data Exchange. No
  Visualization, application framework (OCAF) or Draw.
- **Snapping uses integers.** Once outlines are rounded to an output's grid
  they are integer polygons, and cleaning them up (rounding can make an
  outline cross itself, or pieces touch) is integer polygon work.
  [Clipper2](https://github.com/AngusJohnson/Clipper2) (Boost licence) does
  it, exactly and deterministically. Points are scaled by 1 / grid and
  rounded as KLayout does, so a point exactly halfway rounds the same way in
  both.

### Transforms and instances

**Rigid transforms are applied last.** Booleans, offsets and fillets commute
with moves, rotations and mirrors: offsetting a rotated shape gives the
rotated offset. So:

- `transformed` and `place` never touch vertices. They attach an OCC
  `TopLoc_Location`, which shares the geometry.
- Nested transforms compose into **one**, applied once, with one rounding
  instead of several.
- A `transform` may also **scale** (uniformly, scale > 0). Booleans and
  offsets still commute with it once the distances are scaled too, so the
  rule holds. But OCC does not allow scaling in a `TopLoc_Location`, so a
  scaled transform is applied to the geometry (a copy, once per prototype)
  instead of attached as a location. Mirroring through a location is to be
  verified in the library's first step.

**Instances, not copies.** A placed cell, an array or a polar array is a
*prototype plus transforms*:

- Instances stay instances for drawing (one cached triangulation, drawn per
  transform) and for GDS/OASIS export (cells and array references, which also
  gives smaller files).
- They are turned into real faces only when an operation needs their
  outline: `flat`, a boolean against them, or merging overlapping ones.

**Layers are independent.** Operations and rule checks are per layer, so
layers are flattened, checked and written **in parallel**, one task per layer
(separate OCC shapes on separate threads). Cross-layer work (extrusion
through the stack, enclosure rules) runs on the finished layers.

### Fast paths before general booleans

OCC's general boolean is the slow operation; see
[Measurements](#measurements). Before using it, the library tries:

1. **Bounding-box pruning:** only tools whose boxes overlap an argument take
   part.
2. **Disjoint cuts:** holes that lie inside a face and do not overlap each
   other, such as release-hole arrays, are added to the face directly as
   inner boundaries, with no boolean. Checking that they don't overlap uses a
   grid of bounding boxes (exact on the boxes; a real boolean only where
   boxes touch).
3. **Disjoint unions:** regions that do not overlap are kept side by side.
   "Several shapes on one layer are one" holds without computing a union.

The general boolean, with all tools in one operation and run in parallel, is
the fallback. Every fast path is tested against it.

### Outputs

Every output chooses its own tolerance.

| Output | How |
|---|---|
| Outlines | Per prototype at a chord tolerance (for the canvas, by zoom level, and for exporters), with the transforms of its instances. |
| Snapped outlines | Curves split at a chord tolerance (default 5 nm), then snapped to a grid (default 1 nm) as integer coordinates, per prototype so the hierarchy survives. Used by every grid-based exporter (GDS, OASIS, DXF) and by rule checks. |
| Snapping report | Comes with the snapped outlines: pieces that vanished, split or merged and holes that closed, joined or formed, each with where, and the area before and after. Computed once in the library, so no exporter has to get it right itself. |
| Rule checks | On the snapped outlines, since that is what the fab checks. Run in Python with KLayout's package, per layer. Rules are project data and rule decks; rule kinds are plugins, like exporters (requirements DRC-6 to DRC-13). |
| STEP, BREP | Layers extruded through the layer stack into solids, exact curves kept. Writing STEP is slow for large designs (see [Measurements](#measurements)), so it is an export, never an interactive step. BREP is fast and is what the mesher reads. |
| Mesh | gmsh, from the solids or the 2D faces. `MeshSettings` holds global and per-layer sizes, refinement regions and distances, and names for physical groups. Optional, because of gmsh's licence. |
| Triangles | For a 3D view (`BRepMesh`), or a filled 2D view. |

### Export modules

**Every file format is a plugin**, and the library knows none of them except
OCC's own BREP and STEP. This is how export already works, and it stays:

- An exporter is a Python class with a `format_name`, a `file_extension` and
  an `export(...)` method, found through the `mems_sketch.exporters`
  entry-point group in `pyproject.toml` or registered at runtime
  (`export/base.py`). A format can live in its own package; adding one needs
  no C++ and no rebuild.
- The built-in exporters are plugins like any other: GDS, OASIS and DXF
  (with KLayout's Python package), JSON, XML and `.mat`, BREP and STEP (OCC's
  writers through the bindings), and the mesh formats (gmsh, an optional
  extra).
- **Exporters take building blocks, not a format-specific API.** An exporter
  receives an export context: the build (cells, placements and arrays, the
  layer map with GDS numbers, the layer stack, points and parameter values)
  and functions for outlines, snapped outlines with their report, solids and
  triangles. It picks what it needs. A GDS exporter writes each prototype's
  snapped outlines as a cell and each placement as a reference; a JSON
  exporter flattens.
- **Exporters declare their options:** whether they use a grid and a chord
  tolerance, whether they keep the hierarchy, whether they need the layer
  stack, plus their own (e.g. a GDS text-label layer). The export dialog
  builds its form from the declaration and `mems-sketch-cli export` turns it
  into flags, so neither has code for a particular format.
- **Every export to a grid returns the snapping report** (requirements
  OUT-3), which the GUI shows and the CLI prints.
- **Layouts keep the hierarchy** (OUT-4). `export/cells.py` writes a
  library cell to GDS, OASIS or DXF: each cell once, arrays as array
  references, each cell's geometry snapped in its own frame. A placement
  stays a reference only if it maps the grid onto itself (no scaling, a
  quarter turn, an offset and array steps on the grid); otherwise it is
  flattened into its parent before snapping, so its geometry is snapped where
  it ends up. Writing with the hierarchy gives the same geometry as snapping
  the flat layout. Snapping cell by cell does not see gaps closing between
  separately placed cells; writing flat does.

Keeping the C++ side free of file formats also keeps KLayout's C++ library
out of the build; mems-sketch already depends on its Python package.

## The engine

The engine turns a project into a `mgeom::Layout`. It uses the library's
public API and nothing else.

### Interface to Python

`mems_sketch._core`, built with [nanobind](https://github.com/wjakob/nanobind).
It is deliberately small: the GUI and scripts never handle OCC objects.

```python
engine = _core.Engine()
engine.load(project_json)  # a whole project, validated by pydantic
engine.update(component_name, def_json)  # an edit: only what depends on it rebuilds

build = engine.build("resonator", params)  # cached; raises with the node path on error
build.layers()  # names of layers with geometry
build.points()  # declared alignment points, µm
build.records()  # per node: frame, shift, bounding box, points
build.outlines(layer, chord_um)  # rings for drawing, as NumPy arrays
build.instances(layer)  # (prototype, transform) pairs for drawing
build.snapped(layer, grid_um=0.001, chord_um=0.005)  # integer rings + snapping report
build.solids(chord_um)  # triangles per layer for a 3D view
build.layout()  # the mgeom layout, for scripts that want the library itself
```

Values cross the boundary as plain data: JSON or dicts in, NumPy arrays and
small records out. Exports go through the plugins (`export.export(project,
path, format_name, ...)`), which read the build. The existing `Geometry`
(KLayout regions) stays available in Python, made from `outlines` or
`snapped`, for code that has not moved yet.

### Evaluation

A component's shape tree is evaluated into a **graph of cached nodes**. Each
node has a fingerprint: a hash of its definition, its inputs' fingerprints,
the parameter values it reads and the process constants it uses. The current
compiler already does this per component; the engine does it per node, so
editing one fillet does not rebuild its siblings.

The engine decides **what becomes a cell**, and the library makes instancing
cheap:

- A component placed with the same parameters is one cell, however often it
  is placed.
- An array copy may depend on its `i` and `j`. The engine reads which
  variables a node's expressions use (expression dependencies are already
  resolved). If the node uses neither, it is built once and placed with
  `place_array`. Otherwise each distinct set of values is built once: a node
  that depends only on `i` is built once per column, not once per cell.
- Modifiers and alignments run in the node's own frame and become transforms
  on the cell, so expensive operations are cached in that frame.

**Layer dependencies.** Shape operations work per layer. `layer_map` is the
only kind that moves geometry between layers; it adds edges to the
dependency graph. An edit that only touches one layer's nodes rebuilds only
that layer.

Shape kinds keep the rule `tests/test_architecture.py` enforces today:
kind-specific behaviour lives in its own file, and nothing else switches on
a kind.

### Scheduling builds

The engine, not the GUI, decides what is built and when (requirements
QS-7 to QS-11).

- **A dependency graph** from every value to what reads it: parameters and
  process constants to expressions, expressions to shapes, shapes to the
  points and alignments that use them, and through `layer_map` from layer
  to layer. A change marks only its dependents as stale. The same graph
  answers "what does this value affect?" for the editor without building
  anything (EDT-8).
- **A build queue with priorities:** the value actually chosen first, then a
  drag's full result, then speculative builds. A newer request for the same
  component supersedes older ones.
- **Cancelling:** every build carries a cancel flag, checked between nodes
  and passed into long OCC operations as a progress indicator
  (`Message_ProgressRange`), so a superseded boolean stops early. A cancelled
  build leaves the cache as it was.
- **Threads:** one pool, sized by the settings (QS-11). Layers of one build
  run in parallel. Speculative builds run on the lowest priority and only
  while the machine has idle cores (system load, not just the pool's), and
  are dropped when that changes.
- **A cost model:** the cache records how long each node took to build.
  Before a speculative build, the engine adds up the recorded times of the
  nodes that would be rebuilt (the stale set from the graph) and skips the
  build if it is above the limit. Nodes never built before count as
  expensive.
- **The cache** is bounded by memory, least recently used first, with
  speculative results evicted before anything that was actually shown.

## Code layout

```
src/
  mems_sketch/            Python, as today; core/ shrinks as parts move to C++
  geom/                   the geometry library (mgeom); no engine includes
    include/mgeom/        public headers: region, cell, layout, outputs
    src/
      occ/                OCC wrappers: faces, locations, offsets, fillets
      boolean/            booleans with the fast paths
      out/                outlines, snapping and its report, triangles, STEP/BREP, mesh
    bindings/             nanobind module mems_sketch._geom
    tests/                C++ unit tests of the library alone
  engine/                 the mems-sketch engine; uses only include/mgeom/
    include/mems/         public headers: engine, build, records
    src/
      expr/               expressions and their dependencies
      model/              the project as read from JSON
      eval/               node graph, fingerprints, cache, alignment order
      shapes/             one file per shape kind, as in core/shapes/kinds/
      modifiers/          array, polar_array, mirror, corners
    bindings/             nanobind module mems_sketch._core
    tests/                C++ unit tests of the engine
```

## Build and packaging

- **CMake** for both layers, as two targets: `mgeom` and `mems_engine`,
  which links `mgeom`. **scikit-build-core** is the Python build backend
  (replacing hatchling), and **nanobind** makes the two Python modules.
- **OCC built from source** with only the modules above (CMake options such
  as `BUILD_MODULE_Visualization=OFF`, `BUILD_MODULE_ApplicationFramework=OFF`,
  `BUILD_MODULE_Draw=OFF`), linked statically into `mgeom`. KLayout is used
  only through its Python package, by the exporters and rule checks. The
  engine links no third-party geometry library.
- **The geometry module is optional in a source install.** The package's
  build (the top `CMakeLists.txt`) makes `mems_sketch._geom` when OCC is
  found and is pure Python otherwise; release wheels require it
  (`MEMS_SKETCH_REQUIRE_GEOMETRY=ON`). Until the engine replaces the
  Python compiler nothing but the exporters' library path needs the module.
- **Prebuilt dependencies in CI.** OCC takes a long time to compile, so CI
  builds it once per version and platform and caches it. A contributor runs
  a script that downloads the same build. Nobody compiles OCC to change a
  shape kind.
- **Wheels** for Linux and Windows (x86-64), and macOS (ARM) when its build
  succeeds, with cibuildwheel (requirements QC-4). `pip install
  mems-sketch` stays the whole installation.
- **Local checks.** The commands in `CONTRIBUTING.md` stay the commands to
  run: `pip install -e ".[dev]"` builds the core incrementally, and `pytest`
  also runs the C++ tests. Keeping that fast matters: several sessions work
  on this repository at once.

## Testing

- **Library tests, alone.** C++ tests of `mgeom` per operation, output and
  fast path, with no engine and no project. Each fast path is also checked
  against the general boolean on random inputs.
- **Engine tests** per shape kind, modifier and expression feature, on the
  real library.
- **The dependency rule:** a check fails the build if anything under
  `src/geom/` includes an engine header.
- **Equivalence with the current backend.** During the migration, every
  example project and every test project is built by both backends, and the
  GDS output must match within 1 nm (XOR of the two, sized by 1 nm, is
  empty). This is the test that lets each step merge.
- **Tolerance:** results keep OCC tolerances below the goal; arcs stay arcs
  through boolean, offset and fillet.
- **Performance tests** with budgets: a 10,000-hole plate, a 1,000-finger
  comb and the examples, rebuilt after a parameter change. They join
  `tests/test_gui_canvas_performance.py`.

## Migration

Each step is its own pull request into `development`, keeps every existing
test passing and updates this document.

1. **Interface first, in Python.** Put the `Engine`/`build` interface in front
   of the current compiler, and move the GUI, editing and exports onto it, so
   nothing outside the backend uses KLayout types. *Done:* placements are
   plain `Transform` values (`core/transform.py`); the GUI, editing, storage
   and the command line use `Geometry`'s methods (polygons as µm rings,
   bounding box, hit test, pieces, difference) instead of its regions, and
   build through `mems_sketch.engine` (`Engine.load`, `Engine.build`,
   `Build.geometry`, `points`, `records`, `outlines`), never the compiler
   or the shape evaluator. `tests/test_architecture.py` keeps it that way.
   Swapping the backend now means implementing `Engine` and `Build`.
2. **Build setup.** CMake, scikit-build-core, nanobind, the OCC build and CI
   caching, the two targets and the dependency check. *Done for the
   library:* `mems_sketch._geom` is built into the package, and wheels for
   Linux, Windows and macOS (`wheels.yml`); the engine's module joins it.
3. **The library's regions and cells:** `Region` primitives, transforms,
   `Cell` with instances and arrays, `flat`, outlines, the Python bindings,
   and snapping with its report. Tested alone, and usable from scripts
   through `mems_sketch._geom`. The GDS/OASIS/DXF exporter moves onto the
   snapped outlines, keeping the hierarchy.
4. **The library's operations:** booleans with the fast paths, offsets,
   per-corner rounding.
5. **Expressions and the model in the engine,** tested against
   `core/expressions.py` on every expression in the tests and examples.
   *Done for expressions:* `src/engine/` (`mems_engine`, Python module
   `mems_sketch._core`, in the wheels) evaluates them with Python's
   arithmetic, and `tests/test_engine_expressions.py` finds the same values,
   names and errors on the examples' expressions and twenty thousand
   generated ones. Next: the project model.
6. **Shape kinds and modifiers in the engine,** on the library. The
   equivalence test runs from here on. Then switch the default backend to
   the C++ engine. The Python geometry code is removed once the C++ engine
   has been the default for one release.
7. **New capabilities:** the layer stack, STEP/BREP, the 3D view, meshing
   with gmsh.

## Open questions

- **Corner editing.** `editing/corners.py` finds corners among the vertices
  of merged polygons. With exact curves a "corner" is an OCC vertex where two
  edges meet at an angle. The command stays, and its matching needs
  redesigning.
- **Imported layouts** are polygons and stay polygons. Should the library
  recognise arcs in them (e.g. from DXF), or leave them as they are?
- **gmsh's licence (GPL).** Meshing should be an optional extra or a separate
  plugin, so the rest of mems-sketch is not affected.
- **Python version support:** a compiled core means wheels per Python
  version (3.11 and 3.12 today).
- **The library's name.** `mgeom` is a working name. It needs a final one
  before the library is released on its own.
- **Releasing the library** as its own package, with its own versioning and
  documentation: when, and whether mems-sketch then depends on the released
  package or keeps building it from this repository.

## Measurements

The decision for OCC is based on a benchmark (October 2026; OCC 8.0.1 through
`cadquery-ocp` 8.0.1; 4 cores; OCC in nm, KLayout at 1 nm). The plates have
4 µm holes on a 10 µm pitch.

| Case | KLayout | OCC, one general boolean | OCC, built directly (disjoint cut) |
|---|---|---|---|
| 1,000 square holes | 0.006 s | 0.5 s | — |
| 5,000 square holes | 0.05 s | 4.3 s | 0.01 s* |
| 10,000 square holes | 0.09 s | 15.4 s | 0.14 s* |
| 10,000 circular holes (KLayout: 128-sided polygons) | 1.3 s | 3.3 s, exact circles | — |
| Comb, 1,000 fingers, fused and merged into one face | — | 1.3 s | — |

\* Excluding the time Python spends creating the hole shapes one by one (0.66 s
and 1.45 s), which the C++ core does not have.

- Running the boolean in parallel, oriented bounding boxes and splitting into
  16 tiles did **not** make the general boolean noticeably faster. Hence the
  fast paths.
- Extruding the 10,000-hole plate into a solid took 0.3 s.
- Writing the plate as BREP: 0.2 s (5,000 holes) and 0.45 s (10,000 holes).
  As STEP: 41 s and 161 s. STEP is an export only.
- All results were valid single faces, and circles stayed exact circles.

The alternative considered was Clipper2 (64-bit integer booleans and offsets,
about as fast as KLayout) with arcs recovered from tagged vertices. It is
faster, but exact curves, tangency cases (fillets are tangent by
construction) and 3D would all be ours to build and maintain. It stays the
fallback if real designs are too slow on OCC even with the fast paths.
