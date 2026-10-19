#pragma once
// The centreline of a wire from one port to another (a route shape,
// mems_sketch.core.shapes.kinds.route): which way it goes, and its bends.

#include <span>

#include "mgeom/types.hpp"
#include "mgeom/wire.hpp"

namespace mgeom {

// One end of a route, as a port gives it: where the wire ends, and the way
// out of the part it ends on (degrees, counter-clockwise from +x). The wire
// leaves the start along that way and comes into the end against it.
struct RouteEnd {
    Point at;
    double angle_deg = 0.0;
};

// Which way a route goes:
//  - straight: from the start through each via point to the end, in straight
//    lines (the ends need not meet their ports along their directions);
//  - l: along the start's direction, then along the end's, with one bend
//    where the two lines meet (ahead of both); no via points;
//  - manhattan: along x and y only, through each via point, with as few
//    bends as it can and then as short as it can; it leaves and meets the
//    ports along their directions, which must be along x or y.
enum class RouteStyle { straight, l, manhattan };

// What a bend is: a sharp corner, an arc of the radius tangent to both
// sides, or a chamfer: cut straight across, the radius along each side from
// the corner (as a corners modifier's chamfer).
enum class Bend { sharp, round, chamfer };

// The route's centreline. ``width`` is the wire's: a wire turning nearer to
// a port than half its width would cut back across the port, so the first
// and last runs are at least that long (and as long as a bend takes).
//
// Throws GeometryError: an l route whose lines do not meet ahead of both
// ends, or with via points; a manhattan route with a port not along x or y,
// or that has no way through with these bends; a straight route that turns
// back on itself; or bends that do not fit their sides (with the largest
// radius that does).
Wire route(RouteEnd start, RouteEnd end, std::span<const Point> via, RouteStyle style, Bend bend, double radius,
           double width);

}  // namespace mgeom
