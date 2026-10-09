#include "mgeom/wire.hpp"

#include <cmath>
#include <numbers>
#include <string>

namespace mgeom {

namespace {

constexpr double kTiny = 1e-12;

Point minus(Point a, Point b) { return {a.x - b.x, a.y - b.y}; }
Point plus(Point a, Point b) { return {a.x + b.x, a.y + b.y}; }
Point scaled(Point a, double s) { return {a.x * s, a.y * s}; }
double dot(Point a, Point b) { return a.x * b.x + a.y * b.y; }
double cross(Point a, Point b) { return a.x * b.y - a.y * b.x; }
double length(Point a) { return std::hypot(a.x, a.y); }
Point left_of(Point d) { return {-d.y, d.x}; }  // turned 90° counter-clockwise

void check(Point p) {
    if (!std::isfinite(p.x) || !std::isfinite(p.y)) {
        throw GeometryError("a wire point is not a finite number");
    }
}

}  // namespace

Wire::Wire(Point start) : start_(start) { check(start); }

Wire& Wire::line_to(Point p) {
    check(p);
    if (length(minus(p, end())) < kTiny) throw GeometryError("a segment has no length");
    segments_.push_back({false, p, {}, true});
    return *this;
}

Wire& Wire::arc_to(Point p, double radius, bool large) {
    check(p);
    if (!std::isfinite(radius) || radius == 0.0) throw GeometryError("an arc needs a radius");
    const Point a = end();
    const Point chord = minus(p, a);
    const double c = length(chord);
    if (c < kTiny) throw GeometryError("an arc has no length");
    const double r = std::abs(radius);
    if (c > 2 * r * (1 + 1e-12)) {
        throw GeometryError("an arc of radius " + std::to_string(r) +
                            " cannot reach a point " + std::to_string(c) + " away");
    }
    const bool ccw = radius > 0;
    const double h = std::sqrt(std::max(0.0, r * r - c * c / 4));
    // The shorter arc counter-clockwise has its centre to the left of the
    // chord; the longer one, or clockwise, to the right.
    const double side = (ccw != large) ? 1.0 : -1.0;
    const Point centre = plus(plus(a, scaled(chord, 0.5)), scaled(left_of(chord), side * h / c));
    segments_.push_back({true, p, centre, ccw});
    return *this;
}

Wire& Wire::arc_through(Point via, Point p) {
    check(via);
    check(p);
    const Point a = end();
    const double d = 2 * cross(minus(via, a), minus(p, a));
    if (std::abs(d) < kTiny) throw GeometryError("an arc's three points are in a line");
    // The circumcentre of a, via and p.
    const Point b = minus(via, a), q = minus(p, a);
    const double bb = dot(b, b), qq = dot(q, q);
    const Point centre = plus(a, {(q.y * bb - b.y * qq) / d, (b.x * qq - q.x * bb) / d});
    segments_.push_back({true, p, centre, d > 0});
    return *this;
}

Wire& Wire::bulge_to(Point p, double bulge) {
    check(p);
    if (!std::isfinite(bulge)) throw GeometryError("a bulge is not a finite number");
    if (bulge == 0.0) return line_to(p);
    const double sweep = 4 * std::atan(std::abs(bulge));
    const double c = length(minus(p, end()));
    const double r = c / 2 / std::sin(sweep / 2);
    return arc_to(p, bulge > 0 ? r : -r, sweep > std::numbers::pi);
}

Point Wire::end_direction() const {
    if (segments_.empty()) throw GeometryError("a wire with no segment has no direction");
    const Segment& s = segments_.back();
    const Point from = segments_.size() > 1 ? segments_[segments_.size() - 2].end : start_;
    if (!s.arc) {
        const Point d = minus(s.end, from);
        return scaled(d, 1 / length(d));
    }
    const Point radial = minus(s.end, s.centre);
    const Point t = left_of(scaled(radial, 1 / length(radial)));
    return s.ccw ? t : scaled(t, -1);
}

Wire& Wire::tangent_arc_to(Point p) {
    check(p);
    const Point t = end_direction();
    const Point a = end();
    const Point q = minus(p, a);
    const double side = dot(q, left_of(t));
    if (std::abs(side) < kTiny * std::max(1.0, length(q))) return line_to(p);
    // The circle tangent to t at a through p: its centre is on the normal at
    // a, at the signed distance |q|² / (2 q·n).
    const double r = dot(q, q) / (2 * side);
    const Point centre = plus(a, scaled(left_of(t), r));
    segments_.push_back({true, p, centre, r > 0});
    return *this;
}

Wire& Wire::turn(double radius, double angle_deg) {
    if (!(radius > 0.0) || !std::isfinite(radius)) throw GeometryError("a turn needs a positive radius");
    if (!std::isfinite(angle_deg) || angle_deg == 0.0) throw GeometryError("a turn needs an angle");
    if (std::abs(angle_deg) >= 360.0) throw GeometryError("a turn is less than a full circle");
    const Point t = end_direction();
    const Point a = end();
    const double sign = angle_deg > 0 ? 1.0 : -1.0;
    const Point centre = plus(a, scaled(left_of(t), sign * radius));
    const double phi = angle_deg * std::numbers::pi / 180.0;
    const Point r0 = minus(a, centre);
    const Point r1 = {r0.x * std::cos(phi) - r0.y * std::sin(phi),
                      r0.x * std::sin(phi) + r0.y * std::cos(phi)};
    segments_.push_back({true, plus(centre, r1), centre, angle_deg > 0});
    return *this;
}

}  // namespace mgeom
