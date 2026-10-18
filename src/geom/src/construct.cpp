// The one constructor (a face from an outline of straight and arc segments)
// and the path factory built on it.

#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakeWire.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <GC_MakeArcOfCircle.hxx>
#include <Standard_Failure.hxx>
#include <TopoDS.hxx>

#include <cmath>
#include <numbers>

#include "region_impl.hpp"

namespace mgeom {

using detail::compound_of;
using detail::to_occ;

namespace {

constexpr double kSmooth = 1e-9;  // radians: segments meeting at less are smooth

Point minus(Point a, Point b) { return {a.x - b.x, a.y - b.y}; }
Point plus(Point a, Point b) { return {a.x + b.x, a.y + b.y}; }
Point scaled(Point a, double s) { return {a.x * s, a.y * s}; }
double length(Point a) { return std::hypot(a.x, a.y); }
Point unit(Point a) { return scaled(a, 1 / length(a)); }
Point left_of(Point d) { return {-d.y, d.x}; }

double angle_of(Point v) { return std::atan2(v.y, v.x); }

// The point halfway along an arc from a to b around centre.
Point arc_middle(Point a, Point b, Point centre, bool ccw) {
    double a0 = angle_of(minus(a, centre)), a1 = angle_of(minus(b, centre));
    if (ccw) {
        while (a1 <= a0) a1 += 2 * std::numbers::pi;
    } else {
        while (a1 >= a0) a1 -= 2 * std::numbers::pi;
    }
    const double mid = (a0 + a1) / 2;
    const double r = length(minus(a, centre));
    return {centre.x + r * std::cos(mid), centre.y + r * std::sin(mid)};
}

// Twice the area a closed wire encloses, positive when it runs
// counter-clockwise: the polygon of its points, plus, for each arc, the
// circular segment between it and its chord.
double twice_signed_area(const Wire& outline) {
    double sum = 0;
    Point from = outline.start();
    for (const Wire::Segment& s : outline.segments()) {
        sum += from.x * s.end.y - s.end.x * from.y;
        if (s.arc) {
            const double r = length(minus(from, s.centre));
            double sweep = angle_of(minus(s.end, s.centre)) - angle_of(minus(from, s.centre));
            if (s.ccw) {
                while (sweep <= 0) sweep += 2 * std::numbers::pi;
            } else {
                while (sweep >= 0) sweep -= 2 * std::numbers::pi;
            }
            sum += r * r * (sweep - std::sin(sweep));
        }
        from = s.end;
    }
    const Point start = outline.start();
    return sum + (from.x * start.y - start.x * from.y);
}

TopoDS_Edge edge_of(Point from, const Wire::Segment& s) {
    try {
        if (!s.arc) return BRepBuilderAPI_MakeEdge(to_occ(from), to_occ(s.end)).Edge();
        const Point mid = arc_middle(from, s.end, s.centre, s.ccw);
        return BRepBuilderAPI_MakeEdge(
                   GC_MakeArcOfCircle(to_occ(from), to_occ(mid), to_occ(s.end)).Value())
            .Edge();
    } catch (const Standard_Failure& e) {
        throw GeometryError(std::string("cannot make a segment: ") + e.what());
    }
}

// The direction a segment leaves its start point in.
Point start_direction(Point from, const Wire::Segment& s) {
    if (!s.arc) return unit(minus(s.end, from));
    const Point t = left_of(unit(minus(from, s.centre)));
    return s.ccw ? t : scaled(t, -1);
}

// The direction a segment arrives at its end point in.
Point end_direction(Point from, const Wire::Segment& s) {
    if (!s.arc) return unit(minus(s.end, from));
    const Point t = left_of(unit(minus(s.end, s.centre)));
    return s.ccw ? t : scaled(t, -1);
}

double turn_between(Point in, Point out) {
    return std::atan2(in.x * out.y - in.y * out.x, in.x * out.x + in.y * out.y);
}

}  // namespace

Region Region::polygon(const Wire& outline) {
    const auto& segments = outline.segments();
    if (segments.empty()) throw GeometryError("a polygon needs at least one segment");
    BRepBuilderAPI_MakeWire make;
    Point from = outline.start();
    for (const Wire::Segment& s : segments) {
        make.Add(edge_of(from, s));
        from = s.end;
    }
    if (length(minus(from, outline.start())) > 1e-12) {
        make.Add(BRepBuilderAPI_MakeEdge(to_occ(from), to_occ(outline.start())).Edge());
    }
    if (!make.IsDone()) throw GeometryError("cannot make an outline of these segments");
    // Faces are made from counter-clockwise outlines: a clockwise one makes a
    // face that faces the other way, and a union never merges faces facing
    // opposite ways (it kept a path's corner piece apart from its bands).
    const TopoDS_Wire wire = twice_signed_area(outline) < 0 ? TopoDS::Wire(make.Wire().Reversed()) : make.Wire();
    BRepBuilderAPI_MakeFace face(wire, /*OnlyPlane=*/true);
    if (!face.IsDone()) throw GeometryError("cannot make a face of this outline");
    if (!BRepCheck_Analyzer(face.Face()).IsValid()) {
        throw GeometryError("the outline is not valid (it crosses or touches itself)");
    }
    return RegionAccess::make(compound_of({face.Face()}));
}

Region Region::path(const Wire& centreline, double width, PathEnds ends, Join join) {
    if (!(width > 0.0) || !std::isfinite(width)) throw GeometryError("a path needs a positive width");
    const auto& segments = centreline.segments();
    if (segments.empty()) throw GeometryError("a path needs at least one segment");
    const double h = width / 2;
    std::vector<Region> pieces;

    // A band along each segment.
    Point from = centreline.start();
    for (const Wire::Segment& s : segments) {
        if (!s.arc) {
            const Point n = scaled(left_of(unit(minus(s.end, from))), h);
            const Point quad[] = {plus(from, n), minus(from, n), minus(s.end, n), plus(s.end, n)};
            pieces.push_back(polygon(quad));
        } else {
            const double r = length(minus(from, s.centre));
            if (r <= h) {
                throw GeometryError("an arc of radius " + std::to_string(r) +
                                    " is too tight for a path " + std::to_string(width) + " wide");
            }
            double a0 = angle_of(minus(from, s.centre)) * 180 / std::numbers::pi;
            double a1 = angle_of(minus(s.end, s.centre)) * 180 / std::numbers::pi;
            if (!s.ccw) std::swap(a0, a1);
            while (a1 <= a0) a1 += 360;
            pieces.push_back(arc(s.centre, r - h, r + h, a0, a1));
        }
        from = s.end;
    }

    // Corners, joined as asked (see region.hpp).
    from = centreline.start();
    for (size_t k = 0; k + 1 < segments.size(); ++k) {
        const Wire::Segment& in = segments[k];
        const Wire::Segment& out = segments[k + 1];
        const Point p = in.end;
        const Point t_in = end_direction(from, in);
        const Point t_out = start_direction(p, out);
        from = p;
        const double turn = turn_between(t_in, t_out);
        if (std::abs(turn) < kSmooth) continue;
        // The outer side is right of a left turn, left of a right turn.
        const double side = turn > 0 ? -1.0 : 1.0;
        const Point a = plus(p, scaled(left_of(t_in), side * h));
        const Point b = plus(p, scaled(left_of(t_out), side * h));
        if (join == Join::bevel) {
            const Point wedge[] = {p, a, b};
            pieces.push_back(polygon(wedge));
            continue;
        }
        if (join == Join::round || in.arc || out.arc) {
            // The sector between the bands' ends, not a whole circle: a circle
            // runs through the bands' corners and touches their sides, which
            // the union did not always merge (a sharp turn came out in pieces).
            const Point outward = plus(minus(a, p), minus(b, p));
            const Point towards = length(outward) > 1e-9 * h ? unit(outward) : t_in;  // a U-turn: ahead
            pieces.push_back(polygon(Wire(p).line_to(a).arc_through(plus(p, scaled(towards, h)), b)));
            continue;
        }
        // A miter: the outer edges run on by up to half the width past the
        // corner. Up to a 90° turn they meet within that (a sharp corner);
        // beyond, the corner is cut straight between their ends, so a sharp
        // turn does not spike out.
        if (std::abs(turn) <= std::numbers::pi / 2 + kSmooth) {
            const Point bisector = unit(plus(minus(a, p), minus(b, p)));
            const Point tip = plus(p, scaled(bisector, h / std::cos(turn / 2)));
            const Point wedge[] = {p, a, tip, b};
            pieces.push_back(polygon(wedge));
        } else {
            const Point wedge[] = {p, a, plus(a, scaled(t_in, h)), minus(b, scaled(t_out, h)), b};
            pieces.push_back(polygon(wedge));
        }
    }

    // Ends.
    if (ends != PathEnds::flush) {
        Point before = centreline.start();
        for (size_t k = 0; k + 1 < segments.size(); ++k) before = segments[k].end;
        const Point start = centreline.start();
        const Point end = segments.back().end;
        const Point t0 = start_direction(start, segments.front());
        const Point t1 = end_direction(before, segments.back());
        if (ends == PathEnds::round) {
            pieces.push_back(circle(start, h));
            pieces.push_back(circle(end, h));
        } else {
            for (const auto& [p, t] : {std::pair{start, scaled(t0, -1)}, std::pair{end, t1}}) {
                const Point n = scaled(left_of(t), h);
                const Point cap[] = {plus(p, n), minus(p, n), plus(minus(p, n), scaled(t, h)),
                                     plus(plus(p, n), scaled(t, h))};
                pieces.push_back(polygon(cap));
            }
        }
    }
    return unite(pieces);
}

}  // namespace mgeom
