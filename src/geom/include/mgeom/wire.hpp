#pragma once

#include <vector>

#include "mgeom/types.hpp"

namespace mgeom {

// A chain of straight and circular segments: the outline of a polygon
// (Region::polygon) or the centreline of a path (Region::path). Plain data,
// built one segment at a time from a start point:
//
//   Wire(start).line_to({10, 0}).turn(5, 90).line_to({15, 20})
//
// Arcs are exact. Splines will come as another kind of segment.
class Wire {
public:
    struct Segment {
        bool arc = false;
        Point end;
        Point centre;      // arcs only
        bool ccw = true;   // arcs only: counter-clockwise
    };

    explicit Wire(Point start);

    Wire& line_to(Point p);
    // An arc to p of the given radius, counter-clockwise for radius > 0 and
    // clockwise for radius < 0; the shorter of the two such arcs, or the
    // longer one with large. Throws if p is further than 2 |radius| away.
    Wire& arc_to(Point p, double radius, bool large = false);
    // The arc through via to p.
    Wire& arc_through(Point via, Point p);
    // An arc to p given by its bulge, as in DXF: tan(sweep / 4), positive
    // counter-clockwise. A bulge of 1 is a half circle.
    Wire& bulge_to(Point p, double bulge);
    // The arc to p that continues the previous segment's direction smoothly
    // (a straight line if p is straight ahead).
    Wire& tangent_arc_to(Point p);
    // Turn by angle_deg (positive to the left) along an arc of radius,
    // continuing the previous segment's direction smoothly.
    Wire& turn(double radius, double angle_deg);

    Point start() const { return start_; }
    Point end() const { return segments_.empty() ? start_ : segments_.back().end; }
    const std::vector<Segment>& segments() const { return segments_; }
    // The direction (a unit vector) at the end of the last segment; throws if
    // there is none.
    Point end_direction() const;

private:
    Point start_;
    std::vector<Segment> segments_;
};

// How a path ends: cut off at its end points (flush), extended by half its
// width (square), or with a half disc (round).
enum class PathEnds { flush, square, round };

}  // namespace mgeom
