#pragma once
// Plain value types of the public API. Lengths are in micrometres.

#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace mgeom {

struct Point {
    double x = 0.0;
    double y = 0.0;

    friend bool operator==(const Point&, const Point&) = default;
};

// An axis-aligned box. A default-constructed box is empty.
struct Box {
    double x0 = std::numeric_limits<double>::infinity();
    double y0 = std::numeric_limits<double>::infinity();
    double x1 = -std::numeric_limits<double>::infinity();
    double y1 = -std::numeric_limits<double>::infinity();

    bool empty() const { return x0 > x1 || y0 > y1; }
    double width() const { return empty() ? 0.0 : x1 - x0; }
    double height() const { return empty() ? 0.0 : y1 - y0; }
};

// One closed ring of points, not repeated at the end.
using Ring = std::vector<Point>;

// A polygon with holes, as an output splits a face into points.
struct Polygon {
    Ring hull;
    std::vector<Ring> holes;
};

// How an offset joins the offset edges at a corner: extended until they meet
// (miter: square corners stay square), an arc around the corner (round), or
// the arc's chord (bevel).
enum class Join { miter, round, bevel };

// A corner of a region's boundary: a vertex where two edges meet at an angle
// (not where a line runs smoothly into an arc).
struct Corner {
    Point at;
    bool convex = true;   // material inside the angle smaller than 180°
    double angle_deg = 0.0;  // the angle the boundary turns by there
};

// One corner to round or chamfer, found by its position.
enum class CornerStyle { round, chamfer };

struct CornerRounding {
    Point at;
    double radius = 0.0;  // for a chamfer: the length cut from each edge
    CornerStyle style = CornerStyle::round;
};

// An operation that could not produce valid geometry. Never silently wrong
// geometry instead (requirement QR-3).
class GeometryError : public std::runtime_error {
public:
    explicit GeometryError(const std::string& what) : std::runtime_error(what) {}
};

}  // namespace mgeom
