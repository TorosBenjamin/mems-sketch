#pragma once
// Internals shared by the library's sources: never installed, never
// included by users of the library.

#include <BRep_Builder.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Compound.hxx>
#include <TopoDS_Face.hxx>
#include <gp_Pnt.hxx>

#include <memory>
#include <vector>

#include "mgeom/region.hpp"

namespace mgeom {

namespace detail {

// Geometry is kept in nanometres; the API is in micrometres.
constexpr double kNmPerUm = 1000.0;

inline gp_Pnt to_occ(Point p) { return gp_Pnt(p.x * kNmPerUm, p.y * kNmPerUm, 0.0); }
inline Point from_occ(const gp_Pnt& p) { return {p.X() / kNmPerUm, p.Y() / kNmPerUm}; }

inline TopoDS_Compound compound_of(const std::vector<TopoDS_Face>& faces) {
    TopoDS_Compound compound;
    BRep_Builder builder;
    builder.MakeCompound(compound);
    for (const auto& face : faces) builder.Add(compound, face);
    return compound;
}

inline std::vector<TopoDS_Face> faces_of(const TopoDS_Shape& shape) {
    std::vector<TopoDS_Face> faces;
    if (shape.IsNull()) return faces;
    for (TopExp_Explorer e(shape, TopAbs_FACE); e.More(); e.Next()) {
        faces.push_back(TopoDS::Face(e.Current()));
    }
    return faces;
}

}  // namespace detail

// A region is a compound of faces in the plane z = 0, in nanometres.
struct Region::Impl {
    TopoDS_Shape shape;  // a compound; null or without faces when empty
    std::vector<TopoDS_Face> faces;

    explicit Impl(TopoDS_Shape s) : shape(std::move(s)), faces(detail::faces_of(shape)) {}
};

// Access to a region's Open CASCADE shape for the library's own sources.
struct RegionAccess {
    static const Region::Impl& impl(const Region& r) { return *r.impl_; }
    static Region make(const TopoDS_Shape& shape) {
        return Region(std::make_shared<const Region::Impl>(shape));
    }
};

}  // namespace mgeom
