#include "mgeom/region.hpp"

#include <BRepAdaptor_Curve.hxx>
#include <BRepAlgoAPI_Common.hxx>
#include <BRepAlgoAPI_Cut.hxx>
#include <BRepAlgoAPI_Fuse.hxx>
#include <BRepBndLib.hxx>
#include <BRepBuilderAPI_Copy.hxx>
#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRepBuilderAPI_MakeWire.hxx>
#include <BRepBuilderAPI_Transform.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepClass_FaceClassifier.hxx>
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
#include <BRepAdaptor_Surface.hxx>
#include <ShapeUpgrade_UnifySameDomain.hxx>
#include <Precision.hxx>
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

// Independent copies of shapes for a boolean. Placed copies share their
// geometry (locations of one shape), which keeps instances cheap, but Open
// CASCADE's booleans and face merging are many times slower on shared
// geometry: cutting 1,024 placed holes took 3.1 s, and 0.4 s after copying
// them (the copy: 0.04 s).
NCollection_List<TopoDS_Shape> copied(const NCollection_List<TopoDS_Shape>& shapes) {
    NCollection_List<TopoDS_Shape> result;
    for (const TopoDS_Shape& s : shapes) result.Append(BRepBuilderAPI_Copy(s).Shape());
    return result;
}

// Open CASCADE intersects separate arguments with each other, but not the
// faces inside one compound. So faces that may overlap (a union) go in one
// by one (each); a region's own faces never overlap and go in as one
// compound (one: subtract, intersect).
template <class Op>
TopoDS_Shape run_boolean(const NCollection_List<TopoDS_Shape>& shared_args,
                         const NCollection_List<TopoDS_Shape>& shared_tools, const char* name) {
    try {
        const NCollection_List<TopoDS_Shape> args = copied(shared_args);
        const NCollection_List<TopoDS_Shape> tools = copied(shared_tools);
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

namespace {

Bnd_Box box_of(const TopoDS_Shape& shape) {
    Bnd_Box box;
    BRepBndLib::Add(shape, box, /*useTriangulation=*/false);
    return box;
}

bool strictly_inside(const Bnd_Box& inner, const Bnd_Box& outer) {
    return inner.CornerMin().X() > outer.CornerMin().X() &&
           inner.CornerMin().Y() > outer.CornerMin().Y() &&
           inner.CornerMax().X() < outer.CornerMax().X() &&
           inner.CornerMax().Y() < outer.CornerMax().Y();
}

// Subtracting holes needs no boolean when each one lies inside a face
// without touching its edges or another hole: it is added to the face as an
// inner boundary. That is what release holes, slots and perforations are,
// and the general boolean grows faster than the number of holes (18 s for
// 10,000). Faces are cut this way where they can be; the holes that do not
// qualify (they touch an edge, overlap each other, or have holes of their
// own) are left for the general boolean.
// Twice the signed area of a wire, in the frame of a plane: positive when it
// runs counter-clockwise seen from the plane's normal. Arcs are sampled; the
// sign is all that is needed.
double twice_signed_area(const TopoDS_Wire& wire, const gp_Pln& plane) {
    const gp_Ax3& frame = plane.Position();
    const gp_XYZ origin = frame.Location().XYZ();
    const gp_XYZ xd = frame.XDirection().XYZ(), yd = frame.YDirection().XYZ();
    std::vector<std::pair<double, double>> points;
    for (BRepTools_WireExplorer e(wire); e.More(); e.Next()) {
        BRepAdaptor_Curve curve(e.Current());
        const double a = curve.FirstParameter(), b = curve.LastParameter();
        const int n = curve.GetType() == GeomAbs_Line ? 1 : 16;
        const bool reversed = e.Current().Orientation() == TopAbs_REVERSED;
        for (int k = 0; k < n; ++k) {  // the edge's start, not its end
            const double t = reversed ? b - (b - a) * k / n : a + (b - a) * k / n;
            const gp_XYZ p = curve.Value(t).XYZ() - origin;
            points.emplace_back(p.Dot(xd), p.Dot(yd));
        }
    }
    double twice = 0.0;
    for (size_t i = 0, n = points.size(); i < n; ++i) {
        const auto& [x0, y0] = points[i];
        const auto& [x1, y1] = points[(i + 1) % n];
        twice += x0 * y1 - x1 * y0;
    }
    return twice;
}

// The wire, turned to run counter-clockwise (outer) or clockwise (a hole)
// seen from the plane's normal.
TopoDS_Wire running(const TopoDS_Wire& wire, const gp_Pln& plane, bool counter_clockwise) {
    const bool ccw = twice_signed_area(wire, plane) > 0.0;
    return ccw == counter_clockwise ? wire : TopoDS::Wire(wire.Reversed());
}

struct FastCut {
    std::vector<TopoDS_Face> faces;  // the argument's faces, holes added
    std::vector<TopoDS_Face> rest;   // tools for the general boolean
};

FastCut cut_disjoint_holes(const std::vector<TopoDS_Face>& faces,
                           const std::vector<TopoDS_Face>& tools) {
    FastCut out;
    const size_t nt = tools.size();
    std::vector<Bnd_Box> tool_boxes(nt);
    for (size_t i = 0; i < nt; ++i) tool_boxes[i] = box_of(tools[i]);

    // Tools whose boxes touch another tool's are left to the boolean.
    std::vector<bool> usable(nt, true);
    {
        std::vector<size_t> order(nt);
        std::iota(order.begin(), order.end(), size_t{0});
        auto xmin = [&](size_t i) { return tool_boxes[i].CornerMin().X(); };
        auto xmax = [&](size_t i) { return tool_boxes[i].CornerMax().X(); };
        std::sort(order.begin(), order.end(), [&](size_t a, size_t b) {
            return xmin(a) != xmin(b) ? xmin(a) < xmin(b) : a < b;
        });
        std::vector<size_t> active;
        for (size_t i : order) {
            std::erase_if(active, [&](size_t j) { return xmax(j) < xmin(i); });
            for (size_t j : active) {
                if (!tool_boxes[i].IsOut(tool_boxes[j])) usable[i] = usable[j] = false;
            }
            active.push_back(i);
        }
    }
    for (size_t i = 0; i < nt; ++i) {
        // A hole with holes of its own leaves an island: the boolean does it.
        TopExp_Explorer wires(tools[i], TopAbs_WIRE);
        wires.Next();
        if (wires.More()) usable[i] = false;
    }

    std::vector<std::vector<size_t>> holes_of(faces.size());
    std::vector<bool> placed(nt, false);
    for (size_t f = 0; f < faces.size(); ++f) {
        const Bnd_Box face_box = box_of(faces[f]);
        // The face's own edges, to check that a hole does not touch them.
        std::vector<Bnd_Box> edge_boxes;
        for (TopExp_Explorer e(faces[f], TopAbs_EDGE); e.More(); e.Next()) {
            edge_boxes.push_back(box_of(e.Current()));
        }
        for (size_t i = 0; i < nt; ++i) {
            if (!usable[i] || placed[i] || !strictly_inside(tool_boxes[i], face_box)) continue;
            bool touches = false;
            for (const Bnd_Box& eb : edge_boxes) {
                if (!eb.IsOut(tool_boxes[i])) {
                    touches = true;
                    break;
                }
            }
            if (touches) continue;
            // Inside the face's material, not inside one of its holes.
            TopExp_Explorer v(tools[i], TopAbs_VERTEX);
            gp_Pnt probe;
            if (v.More()) {
                probe = BRep_Tool::Pnt(TopoDS::Vertex(v.Current()));
            } else {
                continue;
            }
            BRepClass_FaceClassifier where(faces[f], probe, Precision::Confusion());
            if (where.State() != TopAbs_IN) continue;
            holes_of[f].push_back(i);
            placed[i] = true;
        }
    }

    for (size_t f = 0; f < faces.size(); ++f) {
        if (holes_of[f].empty()) {
            out.faces.push_back(faces[f]);
            continue;
        }
        // The face again, on its plane, with every boundary turned the way it
        // must run: the outer one counter-clockwise, holes clockwise, however
        // they came (a mirror reverses a face). Open CASCADE's own fix for
        // this compares every hole with every other: 6.8 s for 10,000.
        const gp_Pln plane = BRepAdaptor_Surface(faces[f]).Plane();
        const TopoDS_Wire outer = BRepTools::OuterWire(faces[f]);
        BRepBuilderAPI_MakeFace make(plane, running(outer, plane, true), /*Inside=*/true);
        for (TopExp_Explorer w(faces[f], TopAbs_WIRE); w.More(); w.Next()) {
            const TopoDS_Wire old_hole = TopoDS::Wire(w.Current());
            if (!old_hole.IsSame(outer)) make.Add(running(old_hole, plane, false));
        }
        for (size_t i : holes_of[f]) {
            make.Add(running(BRepTools::OuterWire(tools[i]), plane, false));
        }
        if (!make.IsDone()) throw GeometryError("cannot add holes to a face");
        out.faces.push_back(make.Face());
    }
    for (size_t i = 0; i < nt; ++i) {
        if (!placed[i]) out.rest.push_back(tools[i]);
    }
    return out;
}

}  // namespace

Region Region::operator-(const Region& other) const {
    if (empty() || other.empty()) return *this;
    // Independent copies, as for a boolean (see copied()).
    const std::vector<TopoDS_Face> faces = faces_of(BRepBuilderAPI_Copy(impl_->shape).Shape());
    const std::vector<TopoDS_Face> tools =
        faces_of(BRepBuilderAPI_Copy(other.impl_->shape).Shape());
    FastCut fast = cut_disjoint_holes(faces, tools);
    const TopoDS_Shape cut = compound_of(fast.faces);
    if (fast.rest.empty()) return Region(make_impl(cut));
    return Region(make_impl(
        run_boolean<BRepAlgoAPI_Cut>(one(cut), one(compound_of(fast.rest)), "subtract")));
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

bool Region::valid() const {
    return empty() || BRepCheck_Analyzer(impl_->shape).IsValid();
}

}  // namespace mgeom
