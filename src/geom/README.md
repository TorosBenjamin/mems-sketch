# mgeom

2D layered layout geometry on [Open CASCADE](https://dev.opencascade.org/):
regions of exact lines and arcs, booleans, transforms and outlines. It is the
bottom layer of mems-sketch's new geometry core
([architecture](../../docs/developer/core-architecture.md),
[requirements](../../docs/developer/requirements.md)). It knows nothing of
projects, parameters or shape kinds, and never includes the engine.

**Status:** in progress. Not yet used by mems-sketch.

## Building

Needs a C++20 compiler, CMake 3.24 or later, Ninja and doctest
(`apt install ninja-build doctest-dev` on Ubuntu).

```bash
src/geom/scripts/build-occt.sh          # once: Open CASCADE into build/deps/ (~13 min on 4 cores)
cmake -S src/geom -B build/geom -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH="$PWD/build/deps/occt-8_0_1"
cmake --build build/geom
ctest --test-dir build/geom --output-on-failure
```

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
| `mgeom/measure.hpp` | `properties` (area, perimeter, centroid, second moments of area), `mass_properties` (volume, mass, rotational inertia for a thickness and density), `distance` (exact minimum, with the closest points), `overlap_area`, `projected_overlap`, `edges` (kind, length, ends, midpoint; centre and radius of arcs) |

Lengths are in µm. Inside, geometry is kept in nm, so Open CASCADE's fixed
point tolerance (10⁻⁷ model units) is 10⁻¹⁰ µm. The tests check that a
2×10⁻⁸ µm gap survives a union and that tolerances stay below 10⁻⁸ µm
(requirement QP-1), and that circles stay exact (QP-2).
