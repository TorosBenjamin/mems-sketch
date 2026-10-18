// The area that joins beam ends (see mgeom/connection.hpp).
#include "mgeom/connection.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <numbers>
#include <numeric>
#include <vector>

#include "mgeom/wire.hpp"

namespace mgeom {

namespace {

constexpr double kParallel = 1e-9;  // sin of the angle between sides taken as parallel
constexpr double kAhead = 1e-9;     // µm: a corner must be this far ahead of both ends

Point plus(Point a, Point b) { return {a.x + b.x, a.y + b.y}; }
Point minus(Point a, Point b) { return {a.x - b.x, a.y - b.y}; }
Point scaled(Point a, double s) { return {a.x * s, a.y * s}; }
double dot(Point a, Point b) { return a.x * b.x + a.y * b.y; }
double cross(Point a, Point b) { return a.x * b.y - a.y * b.x; }
double length(Point a) { return std::hypot(a.x, a.y); }
Point unit(Point a) { return scaled(a, 1 / length(a)); }
Point left_of(Point d) { return {-d.y, d.x}; }

std::string number(double v) {
    char buffer[32];
    std::snprintf(buffer, sizeof buffer, "%.4g", v);
    return buffer;
}

struct End {
    Point at, d, left, a, b;  // middle, way out, its left, the left and right corners
    double width;
    std::string label;
};

}  // namespace

Region connection(std::span<const ConnectionEnd> given, double inner_radius, double radius, double reach_in) {
    if (given.size() < 2) throw GeometryError("a connection joins at least two ends");
    if (!(inner_radius >= 0) || !(radius >= 0)) throw GeometryError("a connection's radii cannot be negative");
    if (!(reach_in > 0)) throw GeometryError("a connection must reach into its beams");
    Point centre{0, 0};
    for (const auto& e : given) {
        if (!(e.width > 0)) throw GeometryError("end '" + e.label + "' has no width: give it one");
        centre = plus(centre, scaled(e.at, 1.0 / given.size()));
    }
    std::vector<End> ends;
    double reach = 0, widest = 0;
    for (const auto& e : given) {
        const double a = e.angle_deg * std::numbers::pi / 180;
        const Point d{std::cos(a), std::sin(a)};
        for (const auto& other : ends)
            if (length(minus(other.at, e.at)) < kAhead && dot(other.d, d) > 1 - kParallel)
                throw GeometryError("ends '" + other.label + "' and '" + e.label + "' are the same");
        if (dot(d, minus(centre, e.at)) <= kAhead)
            throw GeometryError("end '" + e.label + "' points away from the others");
        const Point left = left_of(d);
        ends.push_back({e.at, d, left, plus(e.at, scaled(left, e.width / 2)), minus(e.at, scaled(left, e.width / 2)),
                        e.width, e.label});
        reach = std::max(reach, length(minus(e.at, centre)));
        widest = std::max(widest, e.width);
    }
    // Around the centre, counter-clockwise: each end edge runs from its left
    // corner to its right one.
    std::vector<size_t> order(ends.size());
    std::iota(order.begin(), order.end(), size_t{0});
    std::sort(order.begin(), order.end(), [&](size_t i, size_t j) {
        const Point p = minus(ends[i].at, centre), q = minus(ends[j].at, centre);
        return std::atan2(p.y, p.x) < std::atan2(q.y, q.x);
    });
    reach += widest;  // a corner further than this from the centre would be a spike

    Wire outline(ends[order[0]].a);
    for (size_t k = 0; k < order.size(); ++k) {
        const End& e = ends[order[k]];
        const End& next = ends[order[(k + 1) % order.size()]];
        // Back into the beam and across it, a little narrower than it.
        const double inset = std::min(e.width / 10, reach_in);
        outline.line_to(minus(plus(e.a, scaled(e.left, -inset)), scaled(e.d, reach_in)));
        outline.line_to(minus(plus(e.b, scaled(e.left, inset)), scaled(e.d, reach_in)));
        outline.line_to(e.b);
        // To the next end: where the two sides meet ahead of both, a corner.
        const double sine = cross(e.d, next.d);
        bool corner = std::abs(sine) > kParallel;
        double s = 0, u = 0;
        Point x{};
        if (corner) {
            const Point gap = minus(next.a, e.b);
            s = cross(gap, next.d) / sine;  // along this end's side
            u = cross(gap, e.d) / sine;     // along the next one's
            x = plus(e.b, scaled(e.d, s));
            corner = s > kAhead && u > kAhead && length(minus(x, centre)) <= reach;
        }
        if (corner) {
            const bool convex = cross(e.d, scaled(next.d, -1)) > 0;  // the outline turns left there
            const double r = convex ? radius : inner_radius;
            const Point back1 = scaled(e.d, -1), back2 = scaled(next.d, -1);
            const double alpha = std::acos(std::clamp(dot(back1, back2), -1.0, 1.0));
            if (r == 0 || std::numbers::pi - alpha < 1e-9) {
                outline.line_to(x);
            } else {
                const double t = r / std::tan(alpha / 2);
                if (t > s + kAhead || t > u + kAhead) {
                    const double most = std::min(s, u) * std::tan(alpha / 2);
                    throw GeometryError(std::string(convex ? "radius " : "inner radius ") + number(r) +
                                        " does not fit between ends '" + e.label + "' and '" + next.label +
                                        "': at most " + number(most) + " there");
                }
                const Point t1 = plus(x, scaled(back1, std::min(t, s))), t2 = plus(x, scaled(back2, std::min(t, u)));
                const Point bisector = unit(plus(back1, back2));
                const Point centre_of_arc = plus(x, scaled(bisector, r / std::sin(alpha / 2)));
                if (length(minus(t1, e.b)) > kAhead) outline.line_to(t1);
                outline.arc_through(minus(centre_of_arc, scaled(bisector, r)), t2);
            }
        }
        if (k + 1 < order.size() && length(minus(next.a, outline.end())) > kAhead) outline.line_to(next.a);
    }
    try {
        return Region::polygon(outline);
    } catch (const GeometryError&) {
        throw GeometryError("the connection's outline crosses itself: its ends overlap or face past one another");
    }
}

}  // namespace mgeom
