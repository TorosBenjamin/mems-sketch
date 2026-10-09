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

// An operation that could not produce valid geometry. Never silently wrong
// geometry instead (requirement QR-3).
class GeometryError : public std::runtime_error {
public:
    explicit GeometryError(const std::string& what) : std::runtime_error(what) {}
};

}  // namespace mgeom
