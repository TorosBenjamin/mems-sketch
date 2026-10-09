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
                            │  Open CASCADE (OCC) · KLayout's C++ library · gmsh
```

| Layer | Knows about | Never knows about |
|---|---|---|
| **Geometry library** (`mgeom`, working name) | Layers, faces with exact lines and arcs, cells, instances and arrays, rigid transforms, a layer stack, rules, outputs | Projects, YAML, parameters, expressions, shape kinds, alignment points |
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
| History diffs in words (`core/diff.py`) | Library: rule checks (`process/rules.py`), on exported geometry |
| The CLI and the Python scripting API | Library: exports that need geometry: GDS/OASIS/DXF, STEP, BREP, mesh |
| Exporter plugins that only need data (JSON, XML, `.mat`) | Library: reading GDS/OASIS cells (imported layouts) |

Neither C++ layer imports Python or Qt. Like the Python backend today, both
can be tested and used without a GUI.

## The geometry library

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
  static Region polygon(const Wire& outline);  // lines, arcs and splines

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

// Outputs: none of them changes the layout.
Outlines outlines(const Cell&, LayerId, double chord);          // rings for drawing
Triangles triangles(const Cell&, LayerId, double chord);        // 2D or extruded
SnapReport write_gds(const Layout&, CellRef top, const path&, GridOptions);  // also OASIS, DXF
void write_step(const Layout&, CellRef top, const path&);
void write_brep(const Layout&, CellRef top, const path&);
std::vector<Violation> check(const Layout&, CellRef top, const RuleSet&, GridOptions);
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
- **Only the OCC modules needed**, linked statically: Foundation, Modeling
  Data, Modeling Algorithms, and the STEP part of Data Exchange. No
  Visualization, application framework (OCAF) or Draw.

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
| Outlines | Per prototype at a chord tolerance (for the canvas, by zoom level), with the transforms of its instances. |
| GDS, OASIS, DXF | Curves split at the export's chord tolerance (default 5 nm), snapped to the export's grid (default 1 nm) and merged per layer by KLayout's C++ library. Hierarchy is kept as cells and array references. |
| Snapping report | Each export to a grid reports what snapping removed or changed: features collapsed below the grid, gaps opened or closed, widths changed by more than half a grid step. |
| Rule checks | On the geometry as exported (by default the GDS grid), since that is what the fab checks. KLayout's C++ library, per layer, in parallel. |
| STEP, BREP | Layers extruded through the layer stack into solids, exact curves kept. Writing STEP is slow for large designs (see [Measurements](#measurements)), so it is an export, never an interactive step. BREP is fast and is what the mesher reads. |
| Mesh | gmsh, from the solids or the 2D faces. `MeshSettings` holds global and per-layer sizes, refinement regions and distances, and names for physical groups. Optional, because of gmsh's licence. |
| Triangles | For a 3D view (`BRepMesh`), or a filled 2D view. |

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
build.check()  # rule violations
build.export("gds", path, grid_um=0.001, chord_um=0.005)
build.export("step", path)
build.solids(chord_um)  # triangles per layer for a 3D view
build.layout()  # the mgeom layout, for scripts that want the library itself
```

Values cross the boundary as plain data: JSON or dicts in, NumPy arrays and
small records out. The existing `Geometry` (KLayout regions) stays available
in Python, made from `outlines` or an export, for code that has not moved
yet.

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

## Code layout

```
src/
  mems_sketch/            Python, as today; core/ shrinks as parts move to C++
  geom/                   the geometry library (mgeom); no engine includes
    include/mgeom/        public headers: region, cell, layout, outputs
    src/
      occ/                OCC wrappers: faces, locations, offsets, fillets
      boolean/            booleans with the fast paths
      out/                outlines, triangles, GDS via KLayout, STEP/BREP, rules, mesh
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
  `BUILD_MODULE_Draw=OFF`), linked statically into `mgeom`. KLayout's
  database library is linked the same way for GDS and rule checks. The
  engine links no third-party geometry library.
- **Prebuilt dependencies in CI.** OCC takes a long time to compile, so CI
  builds it once per version and platform and caches it. A contributor runs
  a script that downloads the same build. Nobody compiles OCC to change a
  shape kind.
- **Wheels** for Linux, macOS and Windows with cibuildwheel. `pip install
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
   nothing outside the backend uses KLayout types (today `gui/canvas.py`,
   `gui/tools.py`, `editing/results.py` and others do).
2. **Build setup.** CMake, scikit-build-core, nanobind, the OCC build and CI
   caching, the two targets and the dependency check, with modules that only
   report their version.
3. **The library's regions and cells:** `Region` primitives, transforms,
   `Cell` with instances and arrays, `flat`, outlines, and GDS output. Tested
   alone, and usable from scripts through `mems_sketch._geom`.
4. **The library's operations:** booleans with the fast paths, offsets,
   per-corner rounding, rule checks.
5. **Expressions and the model in the engine,** tested against
   `core/expressions.py` on every expression in the tests and examples.
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
