// Offsets, fillets and corners: operations that change a region's boundary.

#include <BRepAdaptor_Curve.hxx>
#include <BRepAdaptor_Surface.hxx>
#include <BRepBuilderAPI_Copy.hxx>
#include <BRepClass_FaceClassifier.hxx>
#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakeWire.hxx>
#include <BRepFilletAPI_MakeFillet2d.hxx>
#include <BRepOffsetAPI_MakeOffset.hxx>
#include <BRepTools.hxx>
#include <GCPnts_AbscissaPoint.hxx>
#include <BRepTools_WireExplorer.hxx>
#include <BRep_Tool.hxx>
#include <Standard_Failure.hxx>
#include <TopExp.hxx>
#include <TopTools_ShapeMapHasher.hxx>
#include <NCollection_IndexedDataMap.hxx>
#include <Precision.hxx>
#include <TopoDS_Edge.hxx>
#include <TopoDS_Vertex.hxx>
#include <TopoDS_Wire.hxx>
#include <gp_Circ.hxx>
#include <gp_Pln.hxx>
#include <gp_Vec.hxx>

#include <cmath>
#include <numbers>
#include <string>

#include "region_impl.hpp"

namespace mgeom {

using detail::compound_of;
using detail::faces_of;
using detail::from_occ;
using detail::kNmPerUm;

namespace {

// A corner is matched to a requested position within this distance (µm).
constexpr double kCornerMatch = 0.002;
// Edges meeting at less than this angle (radians) run smoothly into each
// other: not a corner.
constexpr double kSmooth = 1e-9;

std::string where(const gp_Pnt& p) {
    const Point q = from_occ(p);
    return "(" + std::to_string(q.x) + ", " + std::to_string(q.y) + ")";
}

TopoDS_Shape copied(const TopoDS_Shape& shape) { return BRepBuilderAPI_Copy(shape).Shape(); }

// The direction of an edge where it starts or ends, along the way the
// boundary runs (its orientation taken into account).
gp_Vec direction(const TopoDS_Edge& edge, bool at_end) {
    BRepAdaptor_Curve curve(edge);
    const bool reversed = edge.Orientation() == TopAbs_REVERSED;
    // Running forward, the end is the last parameter; reversed, the first.
    const double t = (at_end != reversed) ? curve.LastParameter() : curve.FirstParameter();
    gp_Pnt p;
    gp_Vec v;
    curve.D1(t, p, v);
    return reversed ? -v : v;
}

double edge_length(const TopoDS_Edge& edge) {
    BRepAdaptor_Curve curve(edge);
    return GCPnts_AbscissaPoint::Length(curve);
}

struct FaceCorner {
    TopoDS_Vertex vertex;
    gp_Pnt point;
    bool convex;
    double turn;  // radians, positive to the left seen from +z
    gp_Vec in, out;  // unit directions of the boundary arriving and leaving
};

std::vector<FaceCorner> corners_of(const TopoDS_Face& face) {
    std::vector<FaceCorner> result;
    const TopoDS_Wire outer = BRepTools::OuterWire(face);
    std::vector<TopoDS_Wire> wires = {outer};
    for (TopExp_Explorer w(face, TopAbs_WIRE); w.More(); w.Next()) {
        if (!w.Current().IsSame(outer)) wires.push_back(TopoDS::Wire(w.Current()));
    }
    for (const TopoDS_Wire& wire : wires) {
        std::vector<TopoDS_Edge> edges;
        std::vector<TopoDS_Vertex> starts;
        for (BRepTools_WireExplorer e(wire, face); e.More(); e.Next()) {
            edges.push_back(e.Current());
            starts.push_back(e.CurrentVertex());
        }
        const size_t n = edges.size();
        for (size_t k = 0; k < n; ++k) {
            const gp_Vec in = direction(edges[(k + n - 1) % n], /*at_end=*/true);
            const gp_Vec out = direction(edges[k], /*at_end=*/false);
            const double turn = std::atan2(in.X() * out.Y() - in.Y() * out.X(),
                                           in.X() * out.X() + in.Y() * out.Y());
            if (std::abs(turn) < kSmooth || n < 2) continue;
            // Convex when the material fills the smaller angle between the two
            // edges: test a point just inside it, on its bisector. This does
            // not depend on which way the face or the boundary is oriented
            // (a mirror reverses both).
            const gp_Pnt p = BRep_Tool::Pnt(starts[k]);
            gp_Vec bisector = in.Normalized().Reversed() + out.Normalized();
            if (bisector.Magnitude() < kSmooth) continue;  // a cusp
            bisector.Normalize();
            // A step well inside the angle, small against both edges (nm).
            const double step = std::min(
                1.0, 1e-3 * std::min(edge_length(edges[k]), edge_length(edges[(k + n - 1) % n])));
            const gp_Pnt probe = p.Translated(bisector * step);
            BRepClass_FaceClassifier inside(face, probe, Precision::Confusion());
            result.push_back({starts[k], p, inside.State() == TopAbs_IN, turn, in.Normalized(), out.Normalized()});
        }
    }
    return result;
}

// --- Offset ---------------------------------------------------------------

// A single closed boundary, offset as a face of its own: outwards (it
// encloses more) for distance > 0, with round joins. The result is the
// boundaries of what the offset encloses; none when it shrinks away.
std::vector<TopoDS_Wire> offset_loop(const TopoDS_Wire& wire, double distance_nm) {
    const TopoDS_Face face = BRepBuilderAPI_MakeFace(wire, /*OnlyPlane=*/true).Face();
    BRepOffsetAPI_MakeOffset offset(face, GeomAbs_Arc);
    try {
        offset.Perform(distance_nm);
    } catch (const Standard_Failure&) {
        if (distance_nm < 0) return {};  // shrunk away
        throw GeometryError("Open CASCADE cannot offset a boundary");
    }
    if (!offset.IsDone()) {
        if (distance_nm < 0) return {};
        throw GeometryError("Open CASCADE cannot offset a boundary");
    }
    std::vector<TopoDS_Wire> loops;
    for (TopExp_Explorer w(offset.Shape(), TopAbs_WIRE); w.More(); w.Next()) {
        loops.push_back(TopoDS::Wire(w.Current()));
    }
    return loops;
}

// Every point within d (nm) of a face's boundary: a band along each edge (a
// rectangle along a line, a ring sector along an arc) and a disk at each end.
// Built from simple shapes, so it does not fail where an offset collapses
// (a hole shrinking to nothing): the face grown by d is the face and this.
Region boundary_band(const TopoDS_Face& face, double d) {
    const double du = d / kNmPerUm;
    std::vector<Region> parts;
    for (TopExp_Explorer e(face, TopAbs_EDGE); e.More(); e.Next()) {
        const TopoDS_Edge& edge = TopoDS::Edge(e.Current());
        BRepAdaptor_Curve curve(edge);
        TopoDS_Vertex v1, v2;
        TopExp::Vertices(edge, v1, v2);
        const Point p = from_occ(BRep_Tool::Pnt(v1)), q = from_occ(BRep_Tool::Pnt(v2));
        parts.push_back(Region::circle(p, du));
        if (curve.GetType() == GeomAbs_Line) {
            const double dx = q.x - p.x, dy = q.y - p.y, length = std::hypot(dx, dy);
            if (length == 0) continue;
            const double nx = -dy / length * du, ny = dx / length * du;
            const Point band[] = {{p.x + nx, p.y + ny}, {q.x + nx, q.y + ny}, {q.x - nx, q.y - ny}, {p.x - nx, p.y - ny}};
            parts.push_back(Region::polygon(band));
        } else if (curve.GetType() == GeomAbs_Circle) {
            const gp_Circ circle = curve.Circle();
            const Point c = from_occ(circle.Location());
            const double radius = circle.Radius() / kNmPerUm;
            auto angle = [&](const Point& at) { return std::atan2(at.y - c.y, at.x - c.x) * 180 / std::numbers::pi; };
            // Sweep counter-clockwise from p to q, or from q to p: the way that
            // passes the edge's middle.
            const Point mid = from_occ(curve.Value((curve.FirstParameter() + curve.LastParameter()) / 2));
            double from = angle(p), to = angle(q), middle = angle(mid);
            auto ccw = [](double a, double b) { return std::fmod(std::fmod(b - a, 360.0) + 360.0, 360.0); };
            if (ccw(from, middle) > ccw(from, to)) std::swap(from, to);
            double sweep = ccw(from, to);
            if (sweep == 0) sweep = 360;  // a full circle
            parts.push_back(Region::arc(c, std::max(0.0, radius - du), radius + du, from, from + sweep));
        } else {
            throw GeometryError("cannot offset a boundary that is neither lines nor arcs");
        }
    }
    return Region::unite(parts);
}

Region region_of_loop(const TopoDS_Wire& wire) {
    return RegionAccess::make(
        compound_of({BRepBuilderAPI_MakeFace(wire, /*OnlyPlane=*/true).Face()}));
}

// What a set of closed boundaries encloses, nested ones cutting holes
// (even-odd): disjoint loops add, a loop inside another cuts it out.
Region enclosed(const std::vector<TopoDS_Wire>& loops) {
    Region result;
    for (const TopoDS_Wire& loop : loops) result = result ^ region_of_loop(loop);
    return result;
}

}  // namespace

Region Region::offset(double distance, Join join) const {
    if (!std::isfinite(distance)) throw GeometryError("an offset needs a finite distance");
    if (empty() || distance == 0.0) return *this;
    if (distance < 0) {
        // Shrinking is growing the outside: what stays is what the grown outside
        // does not reach. The shape's concave corners are the outside's convex
        // ones, and get the same joins.
        const Box box = bbox();
        const double margin = 2 * std::abs(distance) + 1.0;
        const Region frame = rect(box.x0 - margin, box.y0 - margin, box.x1 + margin, box.y1 + margin);
        return *this - (frame - *this).offset(-distance, join);
    }
    const double d = distance * kNmPerUm;
    std::vector<Region> pieces;
    for (const TopoDS_Face& face : faces_of(copied(impl_->shape))) {
        const TopoDS_Wire outer = BRepTools::OuterWire(face);
        std::vector<TopoDS_Wire> holes;
        for (TopExp_Explorer w(face, TopAbs_WIRE); w.More(); w.Next()) {
            if (!w.Current().IsSame(outer)) holes.push_back(TopoDS::Wire(w.Current()));
        }
        // The round offset: the outer boundary moves out by d, each hole's in
        // (material grows into holes as it grows outwards).
        Region piece = enclosed(offset_loop(outer, d));
        for (const TopoDS_Wire& hole : holes) piece = piece - enclosed(offset_loop(hole, -d));
        if (!piece.valid()) {  // an offset that collapsed (a hole shrinking to nothing)
            piece = RegionAccess::make(compound_of({face})) | boundary_band(face, d);
        }
        pieces.push_back(piece);
        if (join == Join::round) continue;
        // Each convex corner filled out beyond the round join: up to the point
        // where the offset edges meet (miter, for a turn of up to 90°); else
        // with the edges running on by d and joined straight (miter), or cut
        // straight across at d from the corner (bevel). Pieces from the
        // original corners, so the union is right however the joins overlap.
        for (const FaceCorner& corner : corners_of(face)) {
            if (!corner.convex) continue;
            const gp_Vec inside = (corner.out - corner.in);  // into the material, roughly
            gp_Vec n_in(corner.in.Y(), -corner.in.X(), 0), n_out(corner.out.Y(), -corner.out.X(), 0);
            if (n_in.Dot(inside) > 0) n_in.Reverse();  // outwards
            if (n_out.Dot(inside) > 0) n_out.Reverse();
            const double turn = std::abs(corner.turn);
            const gp_Pnt v = corner.point;
            const gp_Pnt a = v.Translated(n_in * d), b = v.Translated(n_out * d);
            std::vector<Point> patch = {from_occ(v), from_occ(a)};
            if (join == Join::miter && turn <= std::numbers::pi / 2 + 1e-9) {
                patch.push_back(from_occ(a.Translated(corner.in * (d * std::tan(turn / 2)))));
            } else {
                const double run = join == Join::miter ? d : d * std::tan(turn / 4);
                patch.push_back(from_occ(a.Translated(corner.in * run)));
                patch.push_back(from_occ(b.Translated(corner.out * -run)));
            }
            patch.push_back(from_occ(b));
            pieces.push_back(polygon(patch));
        }
    }
    return unite(pieces);
}

// --- Fillets and corners --------------------------------------------------

std::vector<Corner> Region::corners() const {
    std::vector<Corner> result;
    for (const TopoDS_Face& face : impl_->faces) {
        for (const FaceCorner& c : corners_of(face)) {
            result.push_back({from_occ(c.point), c.convex, c.turn * 180.0 / std::numbers::pi});
        }
    }
    return result;
}

namespace {

void check_fillet(const BRepFilletAPI_MakeFillet2d& fillet, const gp_Pnt& at, double radius) {
    if (fillet.Status() != ChFi2d_IsDone) {
        throw GeometryError("a radius of " + std::to_string(radius) +
                            " does not fit the corner at " + where(at));
    }
}

TopoDS_Face finished(BRepFilletAPI_MakeFillet2d& fillet) {
    fillet.Build();
    if (!fillet.IsDone()) throw GeometryError("cannot round the corners");
    return TopoDS::Face(fillet.Shape());
}

}  // namespace

Region Region::filleted(double convex_radius, double concave_radius) const {
    if (!(convex_radius >= 0.0) || !(concave_radius >= 0.0)) {
        throw GeometryError("fillet radii cannot be negative");
    }
    if (empty() || (convex_radius == 0.0 && concave_radius == 0.0)) return *this;
    std::vector<TopoDS_Face> result;
    for (const TopoDS_Face& face : faces_of(copied(impl_->shape))) {
        BRepFilletAPI_MakeFillet2d fillet(face);
        bool any = false;
        for (const FaceCorner& c : corners_of(face)) {
            const double r = c.convex ? convex_radius : concave_radius;
            if (r == 0.0) continue;
            fillet.AddFillet(c.vertex, r * kNmPerUm);
            check_fillet(fillet, c.point, r);
            any = true;
        }
        result.push_back(any ? finished(fillet) : face);
    }
    return RegionAccess::make(compound_of(result));
}

Region Region::rounded(std::span<const CornerRounding> wanted) const {
    for (const CornerRounding& w : wanted) {
        if (!(w.radius >= 0.0)) throw GeometryError("a corner radius cannot be negative");
    }
    std::vector<bool> found(wanted.size(), false);
    std::vector<TopoDS_Face> result;
    for (const TopoDS_Face& face : faces_of(copied(impl_->shape))) {
        BRepFilletAPI_MakeFillet2d fillet(face);
        NCollection_IndexedDataMap<TopoDS_Shape, NCollection_List<TopoDS_Shape>,
                                   TopTools_ShapeMapHasher>
            edges_at;
        TopExp::MapShapesAndAncestors(face, TopAbs_VERTEX, TopAbs_EDGE, edges_at);
        bool any = false;
        for (const FaceCorner& c : corners_of(face)) {
            const Point at = from_occ(c.point);
            for (size_t k = 0; k < wanted.size(); ++k) {
                const CornerRounding& w = wanted[k];
                if (found[k] || std::hypot(w.at.x - at.x, w.at.y - at.y) > kCornerMatch) continue;
                found[k] = true;
                if (w.radius == 0.0) break;
                if (w.style == CornerStyle::round) {
                    fillet.AddFillet(c.vertex, w.radius * kNmPerUm);
                } else {
                    const auto& edges = edges_at.FindFromKey(c.vertex);
                    fillet.AddChamfer(TopoDS::Edge(edges.First()), TopoDS::Edge(edges.Last()),
                                      w.radius * kNmPerUm, w.radius * kNmPerUm);
                }
                check_fillet(fillet, c.point, w.radius);
                any = true;
                break;
            }
        }
        result.push_back(any ? finished(fillet) : face);
    }
    for (size_t k = 0; k < wanted.size(); ++k) {
        if (!found[k]) {
            throw GeometryError("(" + std::to_string(wanted[k].at.x) + ", " +
                                std::to_string(wanted[k].at.y) + ") is not a corner of the shape");
        }
    }
    return RegionAccess::make(compound_of(result));
}

}  // namespace mgeom
