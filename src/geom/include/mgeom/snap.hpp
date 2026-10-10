#pragma once
// Snapping to an output's grid (requirements OUT-2, OUT-3): what every
// grid-based export (GDS, OASIS, DXF) and rule checks work on. The exact
// region is not changed; snapping makes a copy in integer grid units and
// reports what the rounding changed.

#include <cstdint>
#include <vector>

#include "mgeom/region.hpp"
#include "mgeom/types.hpp"

namespace mgeom {

// A point in grid units: multiply by the grid to get µm.
struct GridPoint {
    std::int64_t x = 0;
    std::int64_t y = 0;

    friend bool operator==(const GridPoint&, const GridPoint&) = default;
};

using GridRing = std::vector<GridPoint>;

// A polygon on the grid: a counter-clockwise hull with clockwise holes.
struct GridPolygon {
    GridRing hull;
    std::vector<GridRing> holes;
};

// One change snapping made to the shape of the geometry, beyond moving
// edges by up to half a grid step.
enum class SnapChange {
    vanished,     // a piece was smaller than the grid and disappeared
    split,        // a piece came apart (a neck narrower than the grid)
    merged,       // pieces joined (a gap narrower than the grid closed)
    hole_closed,  // a hole was smaller than the grid and filled up
    hole_joined,  // a hole joined another, or opened to the outside
    hole_formed,  // a new hole appeared (a notch's mouth closed)
};

struct SnapEvent {
    SnapChange change;
    Box where;  // µm: the original feature, or the pieces involved
};

struct SnapReport {
    double area_exact = 0.0;    // µm², before snapping (the exact region)
    double area_snapped = 0.0;  // µm², after
    std::vector<SnapEvent> events;

    // Whether snapping changed the shape of anything (any event).
    bool changed_shape() const { return !events.empty(); }
};

struct Snapped {
    double grid = 0.0;  // µm per grid unit
    std::vector<GridPolygon> polygons;
    SnapReport report;
};

// The region on a grid of the given step (µm): curves split so that no
// point is further than chord (µm) from them, every point rounded to the
// nearest grid point, and the result cleaned up as an integer polygon set
// (no self-crossings, no duplicate or collinear points). Deterministic.
// Throws if the grid or chord is not positive, or the region does not fit
// in 62-bit grid coordinates.
Snapped snap(const Region& region, double grid, double chord);

}  // namespace mgeom
