#pragma once
// The area that joins beam ends: a junction of beams with rounded corners,
// made from the ends alone (a connection shape, mems_sketch.core.shapes.kinds.connection).

#include <span>
#include <string>

#include "mgeom/region.hpp"
#include "mgeom/types.hpp"

namespace mgeom {

// One end of a connection, as a port gives it: the middle of a beam's end
// edge, the way out of the beam into the connection (degrees, counter-
// clockwise from +x), the beam's width there, and what errors call it.
struct ConnectionEnd {
    Point at;
    double angle_deg = 0.0;
    double width = 0.0;
    std::string label;
};

// The area joining the ends. The ends are taken in order around their
// centre; between neighbours, the beams' sides run on until they meet, and
// that corner is rounded with inner_radius where the area turns in between
// the beams (concave, the usual case) or radius where it turns out
// (convex); 0 leaves it sharp. Where the sides meet behind an end, never
// (parallel), or far beyond the ends, the gap is closed straight. The area
// reaches reach_in back into each beam (narrower than it, so its edges cross
// the beam's instead of lying on them): the beams must be there that far.
//
// Throws GeometryError naming the ends: fewer than two, one without a
// width, two the same, one pointing away from the others, a radius that
// does not fit between two ends (with the largest that does), or an outline
// that crosses itself (ends that overlap or face past one another).
Region connection(std::span<const ConnectionEnd> ends, double inner_radius, double radius, double reach_in);

}  // namespace mgeom
