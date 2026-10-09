#include "mgeom/region.hpp"

#include <BRepAdaptor_Curve.hxx>
#include <BRepAlgoAPI_Common.hxx>
#include <BRepAlgoAPI_Cut.hxx>
#include <BRepAlgoAPI_Fuse.hxx>
#include <BRepBndLib.hxx>
#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRepBuilderAPI_MakeWire.hxx>
#include <BRepBuilderAPI_Transform.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepGProp.hxx>
#include <BRepTools.hxx>
#include <BRepTools_WireExplorer.hxx>
#include <BRep_Builder.hxx>
#include <BRep_Tool.hxx>
#include <Bnd_Box.hxx>
#include <TopLoc_Location.hxx>
#include <GCPnts_QuasiUniformDeflection.hxx>
#include <GC_MakeArcOfCircle.hxx>
#include <GProp_GProps.hxx>
#include <ShapeUpgrade_UnifySameDomain.hxx>
#include <Standard_Failure.hxx>
#include <TopExp_Explorer.hxx>
#include <NCollection_List.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Compound.hxx>
#include <TopoDS_Face.hxx>
#include <TopoDS_Wire.hxx>
#include <gp_Ax2.hxx>
#include <gp_Circ.hxx>
#include <gp_Pnt.hxx>
#include <gp_Trsf.hxx>

#include <algorithm>
#include <cmath>
#include <numbers>
#include <numeric>

namespace mgeom {

namespace {

constexpr double kNmPerUm = 1000.0;

gp_Pnt to_occ(Point p) { return gp_Pnt(p.x * kNmPerUm, p.y * kNmPerUm, 0.0); }
Point from_occ(const gp_Pnt& p) { return {p.X() / kNmPerUm, p.Y() / kNmPerUm}; }

TopoDS_Compound compound_of(const std::vector<TopoDS_Face>& faces) {
    TopoDS_Compound compound;
    BRep_Builder builder;
    builder.MakeCompound(compound);
    for (const auto& face : faces) builder.Add(compound, face);
    return compound;
}

std::vector<TopoDS_Face> faces_of(const TopoDS_Shape& shape) {
    std::vector<TopoDS_Face> faces;
    if (shape.IsNull()) return faces;
    for (TopExp_Explorer e(shape, TopAbs_FACE); e.More(); e.Next()) {
        faces.push_back(TopoDS::Face(e.Current()));
    }
    return faces;
}

TopoDS_Face face_of_wire(const TopoDS_Wire& wire, const char* what) {
    BRepBuilderAPI_MakeFace make(wire, /*OnlyPlane=*/true);
    if (!make.IsDone()) throw GeometryError(std::string("cannot make a face of the ") + what);
    return make.Face();
}

void check_finite(double v, const char* what) {
    if (!std::isfinite(v)) throw GeometryError(std::string(what) + " is not a finite number");
}

}  // namespace

// A region is a compound of faces in the plane z = 0, in nanometres.
struct Region::Impl {
    TopoDS_Shape shape;  // a compound; null or without faces when empty
    std::vector<TopoDS_Face> faces;

    explicit Impl(TopoDS_Shape s) : shape(std::move(s)), faces(faces_of(shape)) {}
};

namespace {

using Impl = Region::Impl;

std::shared_ptr<const Impl> make_impl(const TopoDS_Shape& shape) {
    return std::make_shared<const Impl>(shape);
}

// Merge faces that share edges into one, so a region has no internal edges
// and the same material always gives the same faces.
TopoDS_Shape unified(const TopoDS_Shape& shape) {
    ShapeUpgrade_UnifySameDomain unify(shape, /*UnifyEdges=*/true, /*UnifyFaces=*/true,
                                       /*ConcatBSplines=*/false);
    unify.Build();
    return compound_of(faces_of(unify.Shape()));
}

// Open CASCADE intersects separate arguments with each other, but not the
// faces inside one compound. So faces that may overlap (a union) go in one
// by one; a region's own faces never overlap, and as one compound they are
// several times faster (subtract, intersect).
NCollection_List<TopoDS_Shape> each(const std::vector<TopoDS_Face>& faces) {
    NCollection_List<TopoDS_Shape> list;
    for (const auto& f : faces) list.Append(f);
    return list;
}

NCollection_List<TopoDS_Shape> one(const TopoDS_Shape& shape) {
    NCollection_List<TopoDS_Shape> list;
    list.Append(shape);
    return list;
}

template <class Op>
TopoDS_Shape run_boolean(const NCollection_List<TopoDS_Shape>& args,
                         const NCollection_List<TopoDS_Shape>& tools, const char* name) {
    try {
        Op op;
        op.SetArguments(args);
        op.SetTools(tools);
        op.SetRunParallel(true);
        op.Build();
        if (!op.IsDone() || op.HasErrors()) {
            throw GeometryError(std::string(name) + " failed in Open CASCADE");
        }
        return unified(op.Shape());
    } catch (const Standard_Failure& e) {
        throw GeometryError(std::string(name) + " failed in Open CASCADE: " +
                            e.what());
    }
}

void add_tolerances(const TopoDS_Shape& shape, TopAbs_ShapeEnum kind, double& worst) {
    for (TopExp_Explorer e(shape, kind); e.More(); e.Next()) {
        const double t = kind == TopAbs_VERTEX ? BRep_Tool::Tolerance(TopoDS::Vertex(e.Current()))
                                               : BRep_Tool::Tolerance(TopoDS::Edge(e.Current()));
        worst = std::max(worst, t);
    }
}

// The points of one wire, in its order, each edge split to within chord_nm.
Ring ring_of(const TopoDS_Wire& wire, const TopoDS_Face& face, double chord_nm) {
    Ring ring;
    for (BRepTools_WireExplorer e(wire, face); e.More(); e.Next()) {
        const TopoDS_Edge& edge = e.Current();
        BRepAdaptor_Curve curve(edge);
        std::vector<Point> points;
        if (curve.GetType() == GeomAbs_Line) {
            points = {from_occ(curve.Value(curve.FirstParameter())),
                      from_occ(curve.Value(curve.LastParameter()))};
        } else {
            GCPnts_QuasiUniformDeflection split(curve, chord_nm);
            if (!split.IsDone()) throw GeometryError("cannot split a curve into points");
            for (int i = 1; i <= split.NbPoints(); ++i) points.push_back(from_occ(split.Value(i)));
        }
        if (edge.Orientation() == TopAbs_REVERSED) std::reverse(points.begin(), points.end());
        // Each edge starts where the previous one ended: keep its start only.
        ring.insert(ring.end(), points.begin(), points.end() - 1);
    }
    return ring;
}

double signed_area(const Ring& ring) {
    double twice = 0.0;
    for (size_t i = 0, n = ring.size(); i < n; ++i) {
        const Point& a = ring[i];
        const Point& b = ring[(i + 1) % n];
        twice += a.x * b.y - b.x * a.y;
    }
    return twice / 2.0;
}

}  // namespace

Region::Region() : impl_(make_impl(TopoDS_Shape())) {}
Region::Region(std::shared_ptr<const Impl> impl) : impl_(std::move(impl)) {}

Region Region::rect(double x0, double y0, double x1, double y1) {
    for (double v : {x0, y0, x1, y1}) check_finite(v, "a rectangle coordinate");
    if (x0 > x1) std::swap(x0, x1);
    if (y0 > y1) std::swap(y0, y1);
    if (x0 == x1 || y0 == y1) return Region();  // no area
    const Point corners[] = {{x0, y0}, {x1, y0}, {x1, y1}, {x0, y1}};
    return polygon(corners);
}

Region Region::polygon(std::span<const Point> points) {
    if (points.size() < 3) throw GeometryError("a polygon needs at least 3 points");
    BRepBuilderAPI_MakePolygon make;
    for (const Point& p : points) {
        check_finite(p.x, "a polygon x");
        check_finite(p.y, "a polygon y");
        make.Add(to_occ(p));
    }
    make.Close();
    if (!make.IsDone()) throw GeometryError("cannot make a polygon of these points");
    const TopoDS_Face face = face_of_wire(make.Wire(), "polygon");
    if (!BRepCheck_Analyzer(face).IsValid()) {
        throw GeometryError("the polygon is not valid (its edges cross or overlap)");
    }
    return Region(make_impl(compound_of({face})));
}

Region Region::circle(Point centre, double radius) {
    check_finite(radius, "a circle radius");
    if (radius <= 0.0) throw GeometryError("a circle needs a positive radius");
    const gp_Circ circ(gp_Ax2(to_occ(centre), gp::DZ()), radius * kNmPerUm);
    const TopoDS_Wire wire = BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(circ)).Wire();
    return Region(make_impl(compound_of({face_of_wire(wire, "circle")})));
}

Region Region::arc(Point centre, double r_in, double r_out, double from_deg, double to_deg) {
    for (double v : {r_in, r_out, from_deg, to_deg}) check_finite(v, "an arc value");
    if (r_in < 0.0 || r_out <= r_in) throw GeometryError("an arc needs 0 <= r_in < r_out");
    double sweep = to_deg - from_deg;
    if (sweep <= 0.0) throw GeometryError("an arc needs to_deg > from_deg");
    if (sweep >= 360.0) {
        const Region outer = circle(centre, r_out);
        return r_in > 0.0 ? outer - circle(centre, r_in) : outer;
    }
    const gp_Pnt c = to_occ(centre);
    auto at = [&](double r, double deg) {
        const double a = deg * std::numbers::pi / 180.0;
        return gp_Pnt(c.X() + r * kNmPerUm * std::cos(a), c.Y() + r * kNmPerUm * std::sin(a), 0.0);
    };
    BRepBuilderAPI_MakeWire wire;
    const double mid = (from_deg + to_deg) / 2.0;
    wire.Add(BRepBuilderAPI_MakeEdge(
                 GC_MakeArcOfCircle(at(r_out, from_deg), at(r_out, mid), at(r_out, to_deg)).Value())
                 .Edge());
    if (r_in > 0.0) {
        wire.Add(BRepBuilderAPI_MakeEdge(at(r_out, to_deg), at(r_in, to_deg)).Edge());
        wire.Add(BRepBuilderAPI_MakeEdge(
                     GC_MakeArcOfCircle(at(r_in, to_deg), at(r_in, mid), at(r_in, from_deg)).Value())
                     .Edge());
        wire.Add(BRepBuilderAPI_MakeEdge(at(r_in, from_deg), at(r_out, from_deg)).Edge());
    } else {
        wire.Add(BRepBuilderAPI_MakeEdge(at(r_out, to_deg), c).Edge());
        wire.Add(BRepBuilderAPI_MakeEdge(c, at(r_out, from_deg)).Edge());
    }
    if (!wire.IsDone()) throw GeometryError("cannot make the outline of an arc");
    return Region(make_impl(compound_of({face_of_wire(wire.Wire(), "arc")})));
}

Region Region::operator|(const Region& other) const {
    if (empty()) return other;
    if (other.empty()) return *this;
    const Region both[] = {*this, other};
    return unite(both);
}

Region Region::unite(std::span<const Region> regions) {
    // Every face with its bounding box (enlarged by its tolerance, so faces
    // that touch count as overlapping).
    std::vector<TopoDS_Face> faces;
    std::vector<Bnd_Box> boxes;
    for (const Region& r : regions) {
        for (const TopoDS_Face& f : r.impl_->faces) {
            Bnd_Box box;
            BRepBndLib::Add(f, box, /*useTriangulation=*/false);
            faces.push_back(f);
            boxes.push_back(box);
        }
    }
    const size_t n = faces.size();
    if (n == 0) return Region();

    // Groups of faces whose boxes touch, found by a sweep along x.
    std::vector<size_t> parent(n);
    std::iota(parent.begin(), parent.end(), size_t{0});
    auto root = [&](size_t i) {
        while (parent[i] != i) i = parent[i] = parent[parent[i]];
        return i;
    };
    std::vector<size_t> order(n);
    std::iota(order.begin(), order.end(), size_t{0});
    auto xmin = [&](size_t i) { return boxes[i].CornerMin().X(); };
    auto xmax = [&](size_t i) { return boxes[i].CornerMax().X(); };
    std::sort(order.begin(), order.end(), [&](size_t a, size_t b) {
        return xmin(a) != xmin(b) ? xmin(a) < xmin(b) : a < b;
    });
    std::vector<size_t> active;
    for (size_t i : order) {
        std::erase_if(active, [&](size_t j) { return xmax(j) < xmin(i); });
        for (size_t j : active) {
            if (!boxes[i].IsOut(boxes[j])) parent[root(i)] = root(j);
        }
        active.push_back(i);
    }

    // Faces alone in their group are kept as they are; each group of more is
    // merged by one boolean. Groups are taken in the order of their first
    // face, so the result does not depend on the sort.
    std::vector<std::vector<size_t>> groups(n);
    for (size_t i = 0; i < n; ++i) groups[root(i)].push_back(i);
    std::vector<TopoDS_Face> result;
    std::vector<bool> done(n, false);
    for (size_t i = 0; i < n; ++i) {
        const size_t g = root(i);
        if (done[g]) continue;
        done[g] = true;
        const auto& members = groups[g];
        if (members.size() == 1) {
            result.push_back(faces[members[0]]);
            continue;
        }
        std::vector<TopoDS_Face> first = {faces[members[0]]};
        std::vector<TopoDS_Face> rest;
        for (size_t k = 1; k < members.size(); ++k) rest.push_back(faces[members[k]]);
        const TopoDS_Shape merged =
            run_boolean<BRepAlgoAPI_Fuse>(each(first), each(rest), "union");
        for (const TopoDS_Face& f : faces_of(merged)) result.push_back(f);
    }
    return Region(make_impl(compound_of(result)));
}

Region Region::operator-(const Region& other) const {
    if (empty() || other.empty()) return *this;
    return Region(make_impl(run_boolean<BRepAlgoAPI_Cut>(one(impl_->shape), one(other.impl_->shape), "subtract")));
}

Region Region::operator&(const Region& other) const {
    if (empty() || other.empty()) return Region();
    return Region(make_impl(run_boolean<BRepAlgoAPI_Common>(one(impl_->shape), one(other.impl_->shape), "intersect")));
}

Region Region::operator^(const Region& other) const {
    return (*this - other) | (other - *this);
}

Region Region::transformed(const Transform& t) const {
    if (empty() || t.is_identity()) return *this;
    if (!(t.scale > 0.0) || !std::isfinite(t.scale)) {
        throw GeometryError("a transform needs a positive scale");
    }
    gp_Trsf trsf;
    if (t.mirror_x) trsf.SetMirror(gp_Ax2(gp::Origin(), gp::DY()));  // y -> -y
    if (t.scale != 1.0) {
        gp_Trsf s;
        s.SetScale(gp::Origin(), t.scale);
        trsf.PreMultiply(s);
    }
    if (t.angle_deg != 0.0) {
        gp_Trsf r;
        r.SetRotation(gp::OZ(), t.angle_deg * std::numbers::pi / 180.0);
        trsf.PreMultiply(r);
    }
    if (t.dx != 0.0 || t.dy != 0.0) {
        gp_Trsf m;
        m.SetTranslation(gp_Vec(t.dx * kNmPerUm, t.dy * kNmPerUm, 0.0));
        trsf.PreMultiply(m);
    }
    if (t.scale == 1.0 && !t.mirror_x) {
        // A rigid motion: a location, which shares the geometry.
        return Region(make_impl(impl_->shape.Moved(TopLoc_Location(trsf))));
    }
    // A copy: Open CASCADE allows neither scaling nor mirroring in a location.
    BRepBuilderAPI_Transform apply(impl_->shape, trsf, /*Copy=*/true);
    if (!apply.IsDone()) throw GeometryError("cannot transform the region");
    return Region(make_impl(compound_of(faces_of(apply.Shape()))));
}

bool Region::empty() const { return impl_->faces.empty(); }

int Region::pieces() const { return static_cast<int>(impl_->faces.size()); }

Box Region::bbox() const {
    Box box;
    if (empty()) return box;
    Bnd_Box bounds;
    BRepBndLib::AddOptimal(impl_->shape, bounds, /*useTriangulation=*/false,
                           /*useShapeTolerance=*/false);
    double x0, y0, z0, x1, y1, z1;
    bounds.Get(x0, y0, z0, x1, y1, z1);
    return {x0 / kNmPerUm, y0 / kNmPerUm, x1 / kNmPerUm, y1 / kNmPerUm};
}

double Region::area() const {
    if (empty()) return 0.0;
    GProp_GProps props;
    BRepGProp::SurfaceProperties(impl_->shape, props);
    return std::abs(props.Mass()) / (kNmPerUm * kNmPerUm);
}

std::vector<Polygon> Region::outlines(double chord) const {
    if (!(chord > 0.0)) throw GeometryError("outlines need a positive chord tolerance");
    std::vector<Polygon> result;
    for (const TopoDS_Face& face : impl_->faces) {
        const TopoDS_Wire outer = BRepTools::OuterWire(face);
        Polygon polygon;
        polygon.hull = ring_of(outer, face, chord * kNmPerUm);
        for (TopExp_Explorer e(face, TopAbs_WIRE); e.More(); e.Next()) {
            const TopoDS_Wire wire = TopoDS::Wire(e.Current());
            if (wire.IsSame(outer)) continue;
            polygon.holes.push_back(ring_of(wire, face, chord * kNmPerUm));
        }
        // Hulls counter-clockwise, holes clockwise, whatever the face's
        // orientation (a mirror reverses it).
        if (signed_area(polygon.hull) < 0.0) std::reverse(polygon.hull.begin(), polygon.hull.end());
        for (Ring& hole : polygon.holes) {
            if (signed_area(hole) > 0.0) std::reverse(hole.begin(), hole.end());
        }
        result.push_back(std::move(polygon));
    }
    return result;
}

double Region::max_tolerance() const {
    double worst = 0.0;
    add_tolerances(impl_->shape, TopAbs_VERTEX, worst);
    add_tolerances(impl_->shape, TopAbs_EDGE, worst);
    return worst / kNmPerUm;
}

}  // namespace mgeom
