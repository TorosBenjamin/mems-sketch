#pragma once
// Booleans and offsets of polygons on a grid (integer coordinates): what rule
// checks run on, since they check the snapped outlines the fab gets. Exact and
// fast (Clipper2); the exact regions (region.hpp) are not involved.

#include <vector>

#include "mgeom/snap.hpp"
#include "mgeom/types.hpp"

namespace mgeom::grid {

enum class Op { unite, intersect, subtract, exclusive };

// The polygons united: no overlaps, hulls counter-clockwise with their holes.
std::vector<GridPolygon> merged(const std::vector<GridPolygon>& polygons);

// a op b, each merged first.
std::vector<GridPolygon> boolean(const std::vector<GridPolygon>& a, const std::vector<GridPolygon>& b, Op op);

// Grown (delta > 0) or shrunk by delta grid units. Miter joins are sharp up to
// twice delta from the corner and squared beyond; round joins keep within
// half a grid unit of the true arc.
std::vector<GridPolygon> offset(const std::vector<GridPolygon>& polygons, double delta, Join join);

// The polygons as rings without holes, for formats that have none (GDS,
// OASIS, DXF): each hole joined to the hull by a zero-width cut, which adds no
// points, so the rings fill exactly what the polygon did. A polygon with more
// than max_points points (cuts counted) is first split along grid lines.
std::vector<GridRing> hole_free(const std::vector<GridPolygon>& polygons, size_t max_points);

}  // namespace mgeom::grid
