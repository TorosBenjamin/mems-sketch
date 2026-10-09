// The Python module mems_sketch._geom: the geometry library as plain Python
// values. Points are (x, y) tuples, outlines NumPy arrays, lengths µm.
// Long operations release the GIL, so other Python threads (the GUI) keep
// running while Open CASCADE works.

#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <nanobind/operators.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

#include <cmath>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include "mgeom/cell.hpp"
#include "mgeom/measure.hpp"
#include "mgeom/region.hpp"
#include "mgeom/snap.hpp"
#include "mgeom/transform.hpp"
#include "mgeom/wire.hpp"

namespace nb = nanobind;
using namespace nb::literals;
using namespace mgeom;

// A point is a pair of numbers in Python: a tuple, a list or a NumPy row in,
// a tuple out.
namespace nanobind::detail {
template <>
struct type_caster<Point> {
    NB_TYPE_CASTER(Point, const_name("tuple[float, float]"))

    bool from_python(handle src, uint8_t, cleanup_list*) noexcept {
        PyObject* seq = PySequence_Fast(src.ptr(), "");
        if (!seq) {
            PyErr_Clear();
            return false;
        }
        bool ok = PySequence_Fast_GET_SIZE(seq) == 2;
        if (ok) {
            const double x = PyFloat_AsDouble(PySequence_Fast_GET_ITEM(seq, 0));
            const double y = PyFloat_AsDouble(PySequence_Fast_GET_ITEM(seq, 1));
            if (PyErr_Occurred()) {
                PyErr_Clear();
                ok = false;
            } else {
                value = {x, y};
            }
        }
        Py_DECREF(seq);
        return ok;
    }

    static handle from_cpp(const Point& p, rv_policy, cleanup_list*) noexcept {
        PyObject* t = PyTuple_New(2);
        if (!t) return nullptr;
        PyTuple_SET_ITEM(t, 0, PyFloat_FromDouble(p.x));
        PyTuple_SET_ITEM(t, 1, PyFloat_FromDouble(p.y));
        return t;
    }
};
}  // namespace nanobind::detail

namespace {

using Points = nb::ndarray<nb::numpy, double, nb::shape<-1, 2>>;
using PointsIn = nb::ndarray<const double, nb::shape<-1, 2>, nb::device::cpu>;

// A ring as an (n, 2) array that owns its data.
Points to_array(const Ring& ring) {
    auto* data = new double[ring.size() * 2];
    for (size_t k = 0; k < ring.size(); ++k) {
        data[2 * k] = ring[k].x;
        data[2 * k + 1] = ring[k].y;
    }
    nb::capsule owner(data, [](void* p) noexcept { delete[] static_cast<double*>(p); });
    return Points(data, {ring.size(), 2}, owner);
}

using GridPoints = nb::ndarray<nb::numpy, std::int64_t, nb::shape<-1, 2>>;

// A grid ring as an (n, 2) int64 array that owns its data.
GridPoints to_array(const GridRing& ring) {
    auto* data = new std::int64_t[ring.size() * 2];
    for (size_t k = 0; k < ring.size(); ++k) {
        data[2 * k] = ring[k].x;
        data[2 * k + 1] = ring[k].y;
    }
    nb::capsule owner(data, [](void* p) noexcept { delete[] static_cast<std::int64_t*>(p); });
    return GridPoints(data, {ring.size(), 2}, owner);
}

std::vector<Point> from_array(const PointsIn& a) {
    std::vector<Point> points(a.shape(0));
    auto v = a.view();
    for (size_t k = 0; k < points.size(); ++k) points[k] = {v(k, 0), v(k, 1)};
    return points;
}

// The faces as (hull, [holes]) tuples of (n, 2) arrays.
template <typename P>
nb::list to_python(const std::vector<P>& polygons) {
    nb::list out;
    for (const P& p : polygons) {
        nb::list holes;
        for (const auto& h : p.holes) holes.append(to_array(h));
        out.append(nb::make_tuple(to_array(p.hull), holes));
    }
    return out;
}

std::string box_repr(const Box& b) {
    if (b.empty()) return "Box()";
    return "Box(" + std::to_string(b.x0) + ", " + std::to_string(b.y0) + ", " +
           std::to_string(b.x1) + ", " + std::to_string(b.y1) + ")";
}

// Cells are shared and immutable; Python holds them through this handle.
struct PyCell {
    CellRef ref;
};

}  // namespace

NB_MODULE(_geom, m) {
    m.doc() = "mgeom, the geometry library of mems-sketch: exact 2D regions on Open CASCADE.";
    m.attr("__version__") = MGEOM_VERSION;

    nb::exception<GeometryError>(m, "GeometryError", PyExc_ValueError);

    nb::enum_<Join>(m, "Join", "How offset edges or path segments meet at a corner.")
        .value("miter", Join::miter)
        .value("round", Join::round)
        .value("bevel", Join::bevel);
    nb::enum_<PathEnds>(m, "PathEnds", "How a path ends.")
        .value("flush", PathEnds::flush)
        .value("square", PathEnds::square)
        .value("round", PathEnds::round);
    nb::enum_<CornerStyle>(m, "CornerStyle")
        .value("round", CornerStyle::round)
        .value("chamfer", CornerStyle::chamfer);
    nb::enum_<EdgeKind>(m, "EdgeKind")
        .value("line", EdgeKind::line)
        .value("arc", EdgeKind::arc)
        .value("curve", EdgeKind::curve);

    nb::class_<Box>(m, "Box", "An axis-aligned box; empty when nothing is in it.")
        .def(nb::init<>())
        .def("__init__",
             [](Box* b, double x0, double y0, double x1, double y1) { new (b) Box{x0, y0, x1, y1}; },
             "x0"_a, "y0"_a, "x1"_a, "y1"_a)
        .def_ro("x0", &Box::x0)
        .def_ro("y0", &Box::y0)
        .def_ro("x1", &Box::x1)
        .def_ro("y1", &Box::y1)
        .def_prop_ro("empty", &Box::empty)
        .def_prop_ro("width", &Box::width)
        .def_prop_ro("height", &Box::height)
        .def("__repr__", &box_repr);

    nb::class_<Transform>(m, "Transform",
                          "Mirror about the x axis (y -> -y), then scale, rotate counter-"
                          "clockwise and move. a * b applies b first.")
        .def(nb::init<>())
        .def("__init__",
             [](Transform* t, double dx, double dy, double angle_deg, bool mirror_x, double scale) {
                 new (t) Transform{dx, dy, angle_deg, mirror_x, scale};
             },
             "dx"_a = 0.0, "dy"_a = 0.0, "angle_deg"_a = 0.0, "mirror_x"_a = false,
             "scale"_a = 1.0)
        .def_rw("dx", &Transform::dx)
        .def_rw("dy", &Transform::dy)
        .def_rw("angle_deg", &Transform::angle_deg)
        .def_rw("mirror_x", &Transform::mirror_x)
        .def_rw("scale", &Transform::scale)
        .def_static("translation", &Transform::translation, "dx"_a, "dy"_a)
        .def_static("rotation", &Transform::rotation, "angle_deg"_a)
        .def("apply", &Transform::apply, "point"_a)
        .def("inverted", &Transform::inverted)
        .def_prop_ro("is_identity", &Transform::is_identity)
        .def_prop_ro("is_rigid", &Transform::is_rigid)
        .def(nb::self * nb::self)
        .def("__repr__", [](const Transform& t) {
            return "Transform(dx=" + std::to_string(t.dx) + ", dy=" + std::to_string(t.dy) +
                   ", angle_deg=" + std::to_string(t.angle_deg) +
                   ", mirror_x=" + (t.mirror_x ? "True" : "False") +
                   ", scale=" + std::to_string(t.scale) + ")";
        });

    nb::class_<Wire::Segment>(m, "Segment", "One segment of a wire: a line, or an arc.")
        .def_ro("arc", &Wire::Segment::arc)
        .def_ro("end", &Wire::Segment::end)
        .def_ro("centre", &Wire::Segment::centre)
        .def_ro("ccw", &Wire::Segment::ccw);

    // Each segment method returns the wire itself, so calls chain:
    // Wire((0, 0)).line_to((10, 0)).turn(5, 90)
    nb::class_<Wire>(m, "Wire",
                     "An outline (for Region.polygon) or centreline (for Region.path) of "
                     "straight and arc segments, built from a start point.")
        .def(nb::init<Point>(), "start"_a)
        .def("line_to", &Wire::line_to, "point"_a, nb::rv_policy::reference_internal)
        .def("arc_to", &Wire::arc_to, "point"_a, "radius"_a, "large"_a = false,
             nb::rv_policy::reference_internal,
             "An arc of the given radius, counter-clockwise for radius > 0; the shorter "
             "one, or the longer with large.")
        .def("arc_through", &Wire::arc_through, "via"_a, "point"_a,
             nb::rv_policy::reference_internal)
        .def("bulge_to", &Wire::bulge_to, "point"_a, "bulge"_a, nb::rv_policy::reference_internal,
             "An arc given by its bulge, tan(sweep / 4), as in DXF.")
        .def("tangent_arc_to", &Wire::tangent_arc_to, "point"_a,
             nb::rv_policy::reference_internal)
        .def("turn", &Wire::turn, "radius"_a, "angle_deg"_a, nb::rv_policy::reference_internal,
             "Turn by angle_deg (positive to the left) along an arc of radius.")
        .def_prop_ro("start", &Wire::start)
        .def_prop_ro("end", &Wire::end)
        .def_prop_ro("segments", &Wire::segments)
        .def_prop_ro("end_direction", &Wire::end_direction)
        .def("__len__", [](const Wire& w) { return w.segments().size(); });

    nb::class_<Corner>(m, "Corner")
        .def_ro("at", &Corner::at)
        .def_ro("convex", &Corner::convex)
        .def_ro("angle_deg", &Corner::angle_deg)
        .def("__repr__", [](const Corner& c) {
            return "Corner(at=(" + std::to_string(c.at.x) + ", " + std::to_string(c.at.y) +
                   "), convex=" + (c.convex ? "True" : "False") +
                   ", angle_deg=" + std::to_string(c.angle_deg) + ")";
        });

    nb::class_<CornerRounding>(m, "CornerRounding", "A corner to round or chamfer, by position.")
        .def("__init__",
             [](CornerRounding* c, Point at, double radius, CornerStyle style) {
                 new (c) CornerRounding{at, radius, style};
             },
             "at"_a, "radius"_a, "style"_a = CornerStyle::round)
        .def_rw("at", &CornerRounding::at)
        .def_rw("radius", &CornerRounding::radius)
        .def_rw("style", &CornerRounding::style);

    nb::class_<Region>(m, "Region",
                       "Faces on one layer bounded by exact lines and arcs, holes allowed. "
                       "Immutable; copies share their geometry.")
        .def(nb::init<>())
        .def_static("polygon", nb::overload_cast<const Wire&>(&Region::polygon), "outline"_a,
                    "The face inside a closed outline of lines and arcs.")
        .def_static(
            "polygon", [](const PointsIn& points) { return Region::polygon(from_array(points)); },
            "points"_a, "The polygon through an (n, 2) array of points.")
        .def_static(
            "polygon",
            [](const std::vector<Point>& points) { return Region::polygon(points); },
            "points"_a, "The polygon through a sequence of (x, y) points.")
        .def_static("rect", &Region::rect, "x0"_a, "y0"_a, "x1"_a, "y1"_a)
        .def_static("circle", &Region::circle, "centre"_a, "radius"_a)
        .def_static("arc", &Region::arc, "centre"_a, "r_in"_a, "r_out"_a, "from_deg"_a,
                    "to_deg"_a, "An annular sector, counter-clockwise; a ring over 360°.")
        .def_static("path", &Region::path, "centreline"_a, "width"_a,
                    "ends"_a = PathEnds::flush, "join"_a = Join::miter,
                    nb::call_guard<nb::gil_scoped_release>())
        .def_static(
            "unite",
            [](const std::vector<Region>& regions) {
                nb::gil_scoped_release release;
                return Region::unite(regions);
            },
            "regions"_a, "The union of many regions; ones that don't touch are only collected.")
        .def("__or__", &Region::operator|, nb::call_guard<nb::gil_scoped_release>())
        .def("__sub__", &Region::operator-, nb::call_guard<nb::gil_scoped_release>())
        .def("__and__", &Region::operator&, nb::call_guard<nb::gil_scoped_release>())
        .def("__xor__", &Region::operator^, nb::call_guard<nb::gil_scoped_release>())
        .def("transformed", &Region::transformed, "transform"_a)
        .def("offset", &Region::offset, "distance"_a, "join"_a = Join::miter,
             nb::call_guard<nb::gil_scoped_release>())
        .def("filleted", &Region::filleted, "convex_radius"_a, "concave_radius"_a = 0.0,
             nb::call_guard<nb::gil_scoped_release>())
        .def(
            "rounded",
            [](const Region& r, const std::vector<CornerRounding>& corners) {
                nb::gil_scoped_release release;
                return r.rounded(corners);
            },
            "corners"_a)
        .def("corners", &Region::corners)
        .def_prop_ro("empty", &Region::empty)
        .def("__bool__", [](const Region& r) { return !r.empty(); })
        .def_prop_ro("pieces", &Region::pieces)
        .def_prop_ro("bbox", &Region::bbox)
        .def_prop_ro("area", &Region::area)
        .def(
            "outlines",
            [](const Region& r, double chord) {
                std::vector<Polygon> polygons;
                {
                    nb::gil_scoped_release release;
                    polygons = r.outlines(chord);
                }
                return to_python(polygons);
            },
            "chord"_a,
            nb::sig("def outlines(self, chord: float) -> list[tuple[numpy.typing.NDArray["
                    "numpy.float64], list[numpy.typing.NDArray[numpy.float64]]]]"),
            "The faces as (hull, [holes]) tuples of (n, 2) arrays, curves split so no point "
            "is further than chord from them. Hulls counter-clockwise, holes clockwise.")
        .def_prop_ro("max_tolerance", &Region::max_tolerance)
        .def("valid", &Region::valid)
        .def("__repr__", [](const Region& r) {
            return "<Region: " + std::to_string(r.pieces()) + " pieces, area " +
                   std::to_string(r.area()) + ">";
        });

    // Measurements.
    nb::class_<Properties>(m, "Properties")
        .def_ro("area", &Properties::area)
        .def_ro("perimeter", &Properties::perimeter)
        .def_ro("centroid", &Properties::centroid)
        .def_ro("ix", &Properties::ix)
        .def_ro("iy", &Properties::iy)
        .def_ro("ixy", &Properties::ixy)
        .def_ro("bbox", &Properties::bbox)
        .def_prop_ro("polar", &Properties::polar);
    m.def("properties", &properties, "region"_a,
          "Area, perimeter, centroid and second moments of area (µm⁴, about the centroid).");

    nb::class_<MassProperties>(m, "MassProperties")
        .def_ro("volume", &MassProperties::volume)
        .def_ro("mass", &MassProperties::mass)
        .def_ro("centroid", &MassProperties::centroid)
        .def_ro("izz", &MassProperties::izz);
    m.def("mass_properties", &mass_properties, "properties"_a, "thickness_um"_a,
          "density_kg_per_m3"_a, "Volume (m³), mass (kg) and inertia (kg m²) of a prism.");

    nb::class_<Distance>(m, "Distance")
        .def_ro("value", &Distance::value)
        .def_ro("a", &Distance::a)
        .def_ro("b", &Distance::b);
    m.def("distance", &distance, "a"_a, "b"_a, nb::call_guard<nb::gil_scoped_release>(),
          "The exact minimum distance between two regions, with the closest points.");
    m.def("overlap_area", &overlap_area, "a"_a, "b"_a, nb::call_guard<nb::gil_scoped_release>());
    m.def("projected_overlap", &projected_overlap, "a"_a, "b"_a, "angle_deg"_a,
          "The length the two regions' projections onto a direction share.");

    nb::class_<Edge>(m, "Edge")
        .def_ro("kind", &Edge::kind)
        .def_ro("start", &Edge::start)
        .def_ro("end", &Edge::end)
        .def_ro("mid", &Edge::mid)
        .def_ro("length", &Edge::length)
        .def_ro("centre", &Edge::centre)
        .def_ro("radius", &Edge::radius);
    m.def("edges", &edges, "region"_a, "The boundary's edges, in the order it runs.");

    // Snapping to an output's grid.
    nb::enum_<SnapChange>(m, "SnapChange", "A change snapping made to the shape of the geometry.")
        .value("vanished", SnapChange::vanished, "A piece smaller than the grid disappeared.")
        .value("split", SnapChange::split, "A piece came apart at a neck narrower than the grid.")
        .value("merged", SnapChange::merged, "Pieces joined across a gap narrower than the grid.")
        .value("hole_closed", SnapChange::hole_closed, "A hole smaller than the grid filled up.")
        .value("hole_joined", SnapChange::hole_joined,
               "A hole joined another, or opened to the outside.")
        .value("hole_formed", SnapChange::hole_formed, "A notch's mouth closed into a hole.");

    nb::class_<SnapEvent>(m, "SnapEvent")
        .def_ro("change", &SnapEvent::change)
        .def_ro("where", &SnapEvent::where, "The box of the feature, µm.")
        .def("__repr__", [](const SnapEvent& e) {
            return "<SnapEvent " + std::string(nb::str(nb::cast(e.change)).c_str()) + " at " +
                   box_repr(e.where) + ">";
        });

    nb::class_<SnapReport>(m, "SnapReport")
        .def_ro("area_exact", &SnapReport::area_exact)
        .def_ro("area_snapped", &SnapReport::area_snapped)
        .def_ro("events", &SnapReport::events)
        .def_prop_ro("changed_shape", &SnapReport::changed_shape);

    nb::class_<Snapped>(m, "Snapped")
        .def_ro("grid", &Snapped::grid, "µm per grid unit.")
        .def_prop_ro(
            "polygons", [](const Snapped& s) { return to_python(s.polygons); },
            nb::sig("def polygons(self) -> list[tuple[numpy.typing.NDArray[numpy.int64], "
                    "list[numpy.typing.NDArray[numpy.int64]]]]"),
            "(hull, [holes]) tuples of (n, 2) int64 arrays in grid units; hulls "
            "counter-clockwise, holes clockwise.")
        .def_ro("report", &Snapped::report);

    m.def("snap", &snap, "region"_a, "grid"_a = 0.001, "chord"_a = 0.005,
          nb::call_guard<nb::gil_scoped_release>(),
          "The region on a grid (µm): curves split at chord, points rounded to the grid, "
          "cleaned up, with a report of what the rounding changed.");

    // Cells.
    nb::class_<PyCell>(m, "Cell",
                       "Regions per layer plus placed cells. Immutable: made by a CellBuilder. "
                       "Placing a cell never copies its geometry.")
        .def_prop_ro("name", [](const PyCell& c) { return c.ref->name(); })
        .def_prop_ro("layers", [](const PyCell& c) { return c.ref->layers(); })
        .def(
            "own", [](const PyCell& c, const std::string& layer) { return c.ref->own(layer); },
            "layer"_a, "The cell's own region on a layer, without placements.")
        .def_prop_ro(
            "placements",
            [](const PyCell& c) {
                std::vector<std::pair<PyCell, Transform>> out;
                for (const auto& p : c.ref->placements()) out.push_back({PyCell{p.cell}, p.transform});
                return out;
            },
            "(cell, transform) per copy, arrays expanded.")
        .def(
            "flat",
            [](const PyCell& c, const std::string& layer) {
                nb::gil_scoped_release release;
                return c.ref->flat(layer);
            },
            "layer"_a, "Everything on a layer, placed cells included, merged. Cached.")
        .def_prop_ro("bbox", [](const PyCell& c) { return c.ref->bbox(); })
        .def("__eq__", [](const PyCell& a, const PyCell& b) { return a.ref == b.ref; })
        .def("__hash__", [](const PyCell& c) { return std::hash<const Cell*>()(c.ref.get()); })
        .def("__repr__", [](const PyCell& c) { return "<Cell " + c.ref->name() + ">"; });

    nb::class_<Cell::Builder>(m, "CellBuilder", "Builds a Cell; each method returns the builder.")
        .def(nb::init<std::string>(), "name"_a)
        .def("add", &Cell::Builder::add, "layer"_a, "region"_a, nb::rv_policy::reference_internal)
        .def(
            "place",
            [](Cell::Builder& b, const PyCell& cell, const Transform& t) -> Cell::Builder& {
                return b.place(cell.ref, t);
            },
            "cell"_a, "transform"_a = Transform{}, nb::rv_policy::reference_internal)
        .def(
            "place_array",
            [](Cell::Builder& b, const PyCell& cell, int columns, int rows, double dx, double dy,
               const Transform& t) -> Cell::Builder& {
                return b.place_array(cell.ref, t, ArraySpec{columns, rows, dx, dy});
            },
            "cell"_a, "columns"_a, "rows"_a, "dx"_a, "dy"_a, "transform"_a = Transform{},
            nb::rv_policy::reference_internal,
            "columns x rows copies, dx and dy apart, after the transform.")
        .def(
            "place_polar",
            [](Cell::Builder& b, const PyCell& cell, int count, Point centre, double step_deg,
               bool rotate, const Transform& t) -> Cell::Builder& {
                return b.place_polar(cell.ref, t, PolarSpec{count, centre, step_deg, rotate});
            },
            "cell"_a, "count"_a, "centre"_a = Point{}, "step_deg"_a = 0.0, "rotate"_a = true,
            "transform"_a = Transform{}, nb::rv_policy::reference_internal,
            "count copies around centre, step_deg apart (0: a full circle).")
        .def("build", [](Cell::Builder& b) { return PyCell{b.build()}; });
}
