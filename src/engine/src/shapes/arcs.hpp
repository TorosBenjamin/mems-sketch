// Arcs as the Python backend draws them: polygons with a number of segments
// (mems_sketch.core.shapes.geometry.segments and arc_points). The engine keeps
// arcs exact; these are for shapes given an explicit number of segments, and
// for circles too small for arcs, where the polygon is what was asked for.
#pragma once

#include <vector>

#include "../eval/context.hpp"
#include "mgeom/types.hpp"

namespace mems {

constexpr double ARC_TOLERANCE_UM = 0.005;
constexpr int MAX_ARC_SEGMENTS = 4096;

// The ``segments`` a node asks for, as Python reads it (none, or 0: none).
double explicit_segments(const Json& node, const Context& ctx);

// Segments per full circle: as asked for, or enough for the arc tolerance.
int segment_count(double radius, double explicit_segments);

// Points on an arc from start to end (degrees), n_full segments per circle; a
// full circle does not repeat its first point.
std::vector<mgeom::Point> arc_points(double cx, double cy, double r, double start, double end, int n_full);

}  // namespace mems
