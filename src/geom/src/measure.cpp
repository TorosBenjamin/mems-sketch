#include "mgeom/measure.hpp"

#include <BRepAdaptor_Curve.hxx>
#include <BRepExtrema_DistShapeShape.hxx>
#include <BRepGProp.hxx>
#include <BRepTools.hxx>
#include <BRepTools_WireExplorer.hxx>
#include <GCPnts_AbscissaPoint.hxx>
#include <GProp_GProps.hxx>
#include <TopoDS_Edge.hxx>
#include <TopoDS_Wire.hxx>
#include <gp_Circ.hxx>
#include <gp_Mat.hxx>

#include <algorithm>
#include <cmath>
#include <numbers>

#include "region_impl.hpp"

namespace mgeom {

using detail::from_occ;
using detail::kNmPerUm;

namespace {

constexpr double kUmPerM = 1e6;

const TopoDS_Shape& shape_of(const Region& r) { return RegionAccess::impl(r).shape; }

}  // namespace

Properties properties(const Region& region) {
    Properties p;
    if (region.empty()) return p;
    const TopoDS_Shape& shape = shape_of(region);

    GProp_GProps surface;
    BRepGProp::SurfaceProperties(shape, surface);
    const double nm2 = kNmPerUm * kNmPerUm;
    p.area = surface.Mass() / nm2;
    p.centroid = from_occ(surface.CentreOfMass());
    // The matrix of inertia about the centre of mass, for a unit density:
    // (1,1) = ∫ y² + z², (2,2) = ∫ x² + z², (1,2) = -∫ x y. Here z = 0.
    const gp_Mat m = surface.MatrixOfInertia();
    p.ix = m(1, 1) / (nm2 * nm2);
    p.iy = m(2, 2) / (nm2 * nm2);
    p.ixy = -m(1, 2) / (nm2 * nm2);

    GProp_GProps lines;
    BRepGProp::LinearProperties(shape, lines);
    p.perimeter = lines.Mass() / kNmPerUm;
    p.bbox = region.bbox();
    return p;
}

MassProperties mass_properties(const Properties& p, double thickness_um,
                               double density_kg_per_m3) {
    if (!(thickness_um >= 0.0) || !(density_kg_per_m3 >= 0.0)) {
        throw GeometryError("thickness and density cannot be negative");
    }
    MassProperties m;
    const double area_m2 = p.area / (kUmPerM * kUmPerM);
    const double thickness_m = thickness_um / kUmPerM;
    m.volume = area_m2 * thickness_m;
    m.mass = m.volume * density_kg_per_m3;
    m.centroid = p.centroid;
    // A prism: every slice has the same polar second moment of area.
    const double polar_m4 = p.polar() / (kUmPerM * kUmPerM * kUmPerM * kUmPerM);
    m.izz = density_kg_per_m3 * thickness_m * polar_m4;
    return m;
}

Distance distance(const Region& a, const Region& b) {
    if (a.empty() || b.empty()) throw GeometryError("a distance needs two regions with geometry");
    BRepExtrema_DistShapeShape extrema(shape_of(a), shape_of(b));
    if (!extrema.IsDone() || extrema.NbSolution() < 1) {
        throw GeometryError("cannot find the distance between the regions");
    }
    Distance d;
    d.value = extrema.Value() / kNmPerUm;
    d.a = from_occ(extrema.PointOnShape1(1));
    d.b = from_occ(extrema.PointOnShape2(1));
    return d;
}

double overlap_area(const Region& a, const Region& b) { return (a & b).area(); }

double projected_overlap(const Region& a, const Region& b, double angle_deg) {
    if (a.empty() || b.empty()) return 0.0;
    // Turn the direction onto the x axis: the projections are the x extents.
    const Transform turn = Transform::rotation(-angle_deg);
    const Box ba = a.transformed(turn).bbox();
    const Box bb = b.transformed(turn).bbox();
    return std::max(0.0, std::min(ba.x1, bb.x1) - std::max(ba.x0, bb.x0));
}

std::vector<Edge> edges(const Region& region) {
    std::vector<Edge> result;
    for (const TopoDS_Face& face : RegionAccess::impl(region).faces) {
        const TopoDS_Wire outer = BRepTools::OuterWire(face);
        std::vector<TopoDS_Wire> wires = {outer};
        for (TopExp_Explorer w(face, TopAbs_WIRE); w.More(); w.Next()) {
            if (!w.Current().IsSame(outer)) wires.push_back(TopoDS::Wire(w.Current()));
        }
        for (const TopoDS_Wire& wire : wires) {
            for (BRepTools_WireExplorer e(wire, face); e.More(); e.Next()) {
                const TopoDS_Edge& edge = e.Current();
                BRepAdaptor_Curve curve(edge);
                const double t0 = curve.FirstParameter(), t1 = curve.LastParameter();
                const bool reversed = edge.Orientation() == TopAbs_REVERSED;
                Edge out;
                out.start = from_occ(curve.Value(reversed ? t1 : t0));
                out.end = from_occ(curve.Value(reversed ? t0 : t1));
                out.mid = from_occ(curve.Value((t0 + t1) / 2));
                out.length = GCPnts_AbscissaPoint::Length(curve) / kNmPerUm;
                switch (curve.GetType()) {
                    case GeomAbs_Line:
                        out.kind = EdgeKind::line;
                        break;
                    case GeomAbs_Circle:
                        out.kind = EdgeKind::arc;
                        out.centre = from_occ(curve.Circle().Location());
                        out.radius = curve.Circle().Radius() / kNmPerUm;
                        break;
                    default:
                        out.kind = EdgeKind::curve;
                }
                result.push_back(out);
            }
        }
    }
    return result;
}

std::vector<Point> convex_hull(std::span<const Point> points) {
    std::vector<Point> sorted(points.begin(), points.end());
    std::sort(sorted.begin(), sorted.end(), [](const Point& a, const Point& b) { return a.x < b.x || (a.x == b.x && a.y < b.y); });
    sorted.erase(std::unique(sorted.begin(), sorted.end()), sorted.end());
    if (sorted.size() < 3) return sorted;
    const auto cross = [](const Point& o, const Point& a, const Point& b) {
        return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
    };
    std::vector<Point> hull(2 * sorted.size());
    size_t k = 0;
    for (const Point& p : sorted) {  // lower half, then upper (Andrew's monotone chain)
        while (k >= 2 && cross(hull[k - 2], hull[k - 1], p) <= 0) --k;
        hull[k++] = p;
    }
    for (size_t i = sorted.size() - 1, lower = k + 1; i-- > 0;) {
        while (k >= lower && cross(hull[k - 2], hull[k - 1], sorted[i]) <= 0) --k;
        hull[k++] = sorted[i];
    }
    hull.resize(k - 1);
    return hull;
}

Point MinimalBox::to_xy(Point uv) const {
    const double a = angle_deg * std::numbers::pi / 180, c = std::cos(a), s = std::sin(a);
    return {uv.x * c - uv.y * s, uv.x * s + uv.y * c};
}

namespace {

Box turned_box(std::span<const Point> points, double angle_deg) {
    const double a = angle_deg * std::numbers::pi / 180, c = std::cos(a), s = std::sin(a);
    Box box;
    for (const Point& p : points) {
        const double u = p.x * c + p.y * s, v = -p.x * s + p.y * c;
        box.x0 = std::min(box.x0, u), box.x1 = std::max(box.x1, u);
        box.y0 = std::min(box.y0, v), box.y1 = std::max(box.y1, v);
    }
    return box;
}

// An edge direction as the angle in (-45, 45] that describes the same rectangles.
double folded(double angle_deg) {
    double a = std::fmod(angle_deg, 90.0);
    if (a <= -45) a += 90;
    if (a > 45) a -= 90;
    return std::abs(a) < 1e-9 ? 0.0 : a;
}

}  // namespace

MinimalBox minimal_box(std::span<const Point> points, double tolerance) {
    const std::vector<Point> hull = convex_hull(points);
    if (hull.empty()) return {};
    // The smallest rectangle has a side on an edge of the hull; the level box
    // is a candidate too, for the ties it wins.
    std::vector<double> angles = {0.0};
    for (size_t i = 0; i < hull.size(); ++i) {
        const Point& a = hull[i];
        const Point& b = hull[(i + 1) % hull.size()];
        if (a == b) continue;
        angles.push_back(folded(std::atan2(b.y - a.y, b.x - a.x) * 180 / std::numbers::pi));
    }
    std::vector<MinimalBox> candidates;
    double best = INFINITY;
    for (const double angle : angles) {
        candidates.push_back({angle, turned_box(hull, angle)});
        best = std::min(best, candidates.back().box.width() * candidates.back().box.height());
    }
    MinimalBox chosen = candidates.front();
    bool found = false;
    for (const MinimalBox& c : candidates) {
        const double w = c.box.width(), h = c.box.height();
        // Points within tolerance of the outline: each side may be off by twice it.
        const double slack = 4 * tolerance * (w + h) + 1e-12 * (1 + w * h);
        if (w * h > best + slack) continue;
        const bool nearer = std::abs(c.angle_deg) < std::abs(chosen.angle_deg) - 1e-12 ||
                            (std::abs(std::abs(c.angle_deg) - std::abs(chosen.angle_deg)) <= 1e-12 &&
                             c.angle_deg > chosen.angle_deg);
        if (!found || nearer) chosen = c, found = true;
    }
    return chosen;
}

std::vector<Point> outline_points(const Region& region, double chord) {
    std::vector<Point> points;
    for (const Polygon& polygon : region.outlines(chord)) points.insert(points.end(), polygon.hull.begin(), polygon.hull.end());
    return points;
}

}  // namespace mgeom
