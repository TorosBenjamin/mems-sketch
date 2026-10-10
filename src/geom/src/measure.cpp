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

}  // namespace mgeom
