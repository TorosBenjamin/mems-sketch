# mgeom

2D layered layout geometry on [Open CASCADE](https://dev.opencascade.org/):
regions of exact lines and arcs, booleans, transforms and outlines. It is the
bottom layer of mems-sketch's new geometry core
([architecture](../../docs/developer/core-architecture.md),
[requirements](../../docs/developer/requirements.md)). It knows nothing of
projects, parameters or shape kinds, and never includes the engine.

**Status:** in progress. Not yet used by mems-sketch.

## Building

Needs a C++20 compiler, CMake 3.28 or later, Ninja and doctest
(`apt install ninja-build doctest-dev` on Ubuntu). CMake fetches
[Clipper2](https://github.com/AngusJohnson/Clipper2) at a fixed commit when
configuring.

```bash
src/geom/scripts/build-occt.sh          # once: Open CASCADE into build/deps/ (~13 min on 4 cores)
python3 -m venv build/pyenv             # once: a Python for the bindings
build/pyenv/bin/pip install nanobind numpy pytest
cmake -S src/geom -B build/geom -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH="$PWD/build/deps/occt-8_0_1" \
  -DMGEOM_BUILD_PYTHON=ON -DPython_EXECUTABLE="$PWD/build/pyenv/bin/python"
cmake --build build/geom
ctest --test-dir build/geom --output-on-failure
```

Without `-DMGEOM_BUILD_PYTHON=ON` only the C++ library and its tests are
built. With it, the build also makes the Python module `_geom` and its type
stubs (`_geom.pyi`) in `build/geom/`, and ctest also runs the Python tests in
`tests/python/`.

### In the package

Installing mems-sketch (`pip install -e .`, or `uv run`) builds the module
into the package as **`mems_sketch._geom`** (scikit-build-core, from the
repository's top `CMakeLists.txt`) whenever Open CASCADE is found: in
`build/deps/`, where the script above puts it, or on `CMAKE_PREFIX_PATH`.
Without it the package is pure Python and everything but the library works,
so nobody needs a compiler to work on the rest. The build is kept in
`build/python/`, so a reinstall only compiles what changed.

A build that must include the module sets
`SKBUILD_CMAKE_DEFINE="MEMS_SKETCH_REQUIRE_GEOMETRY=ON"`, and then fails
without Open CASCADE instead of leaving the module out. The release wheels
(`.github/workflows/wheels.yml`, cibuildwheel) do: Linux and Windows on
x86-64, macOS on Apple silicon (requirement QC-4), Python 3.11 to 3.13, each
tested by importing the module and running `tests/test_cells_export.py`.

Open CASCADE is built from source, as static libraries of only the modules
the library uses. CI caches the build. Ubuntu's own packages (7.6.3) cannot
be used: they miss a header (`NCollection_AliasedArray.hxx`) that their other
headers include.

## What it has so far

| | |
|---|---|
| `mgeom/types.hpp` | `Point`, `Box`, `Polygon` (a hull with holes), `GeometryError` |
| `mgeom/transform.hpp` | `Transform`: mirror, scale, rotate, move; composes with `*` |
| `mgeom/region.hpp` | `Region`: rect, polygon, circle, arc; union, subtract, intersect, xor; transformed; area, bounding box, outlines at a chord tolerance, the largest Open CASCADE tolerance it carries |
| `mgeom/wire.hpp` | `Wire`: an outline or centreline of straight and arc segments (`line_to`, `arc_to` by radius, `arc_through`, `bulge_to`, `tangent_arc_to`, `turn`) |
| `Region` from wires | `polygon(wire)`, the one constructor every shape is made by; `path(centreline, width, ends, join)` with miter, round or bevel corners and flush, square or round ends |
| `Region` editing | `offset(distance, join)` (miter, round, bevel; grows, shrinks, splits and merges), `filleted(convex, concave)`, `rounded(corners)` (chosen corners rounded or chamfered), `corners()` |
| `mgeom/cell.hpp` | `Cell`: regions per layer plus placed cells, arrays and polar arrays; placing never copies geometry; `flat(layer)` merges and caches |
| `mgeom/snap.hpp` | `snap(region, grid, chord)`: the region on an output's grid as integer polygons, cleaned up, with a report of what the rounding changed (pieces vanished, split or merged; holes closed, joined or formed; area before and after) |
| `mgeom/measure.hpp` | `properties` (area, perimeter, centroid, second moments of area), `mass_properties` (volume, mass, rotational inertia for a thickness and density), `distance` (exact minimum, with the closest points), `overlap_area`, `projected_overlap`, `edges` (kind, length, ends, midpoint; centre and radius of arcs) |

## From Python

The module `_geom` ([bindings/geom.cpp](bindings/geom.cpp)) has the same API
as plain Python values: points are `(x, y)` tuples (lists and NumPy rows are
accepted), polygons can be given as `(n, 2)` arrays, outlines come back as
`(hull, [holes])` tuples of `(n, 2)` NumPy arrays, and `GeometryError` is a
`ValueError`. Segment and builder methods return the object itself, so calls
chain. Booleans, offsets, fillets, paths, outlines and `flat` release the GIL,
so a GUI thread keeps running while they work.

```python
from mems_sketch import _geom as g

slot = g.Wire((0, -1)).line_to((10, -1)).arc_to((10, 1), 1).line_to((0, 1)).arc_to((0, -1), 1)
hole = g.CellBuilder("hole").add("etch", g.Region.polygon(slot)).build()
plate = (
    g.CellBuilder("plate")
    .add("device", g.Region.rect(0, 0, 200, 100))
    .place_array(hole, 10, 20, 15, 5, g.Transform(dx=20, dy=2))
    .build()
)
for hull, holes in plate.flat("etch").outlines(chord=0.005):
    ...
```

A cell's `references` are its placements as they were made, arrays kept
as arrays. `mems_sketch.export.cells.write_cell(cell, path, layers)` writes a
cell to GDS, OASIS or DXF on a grid, keeping cells and array references, and
returns the snapping report; `tests/test_cells_export.py` runs when the
module is installed (or on `PYTHONPATH`) and skips otherwise.

In the package it is `from mems_sketch import _geom`; built by hand as above,
it is `import _geom` from `build/geom/`.

Lengths are in µm. Inside, geometry is kept in nm, so Open CASCADE's fixed
point tolerance (10⁻⁷ model units) is 10⁻¹⁰ µm. The tests check that a
2×10⁻⁸ µm gap survives a union and that tolerances stay below 10⁻⁸ µm
(requirement QP-1), and that circles stay exact (QP-2).
