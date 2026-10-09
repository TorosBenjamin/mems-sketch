#pragma once

#include <memory>
#include <span>
#include <vector>

#include "mgeom/transform.hpp"
#include "mgeom/types.hpp"

namespace mgeom {

// Faces on one layer, bounded by exact lines and circular arcs, holes
// allowed. Overlapping faces are merged: a region has no internal edges.
//
// Immutable and cheap to copy: copies share their geometry. The public API
// is in micrometres; inside, geometry is kept in nanometres, so Open
// CASCADE's fixed point tolerance (1e-7 model units) is 1e-10 µm.
class Region {
public:
    Region();  // empty

    static Region rect(double x0, double y0, double x1, double y1);
    static Region polygon(std::span<const Point> points);
    static Region circle(Point centre, double radius);
    // An annular sector from from_deg to to_deg, counter-clockwise; a ring
    // when it spans 360°. r_in may be 0 (a pie slice, or a disc).
    static Region arc(Point centre, double r_in, double r_out, double from_deg, double to_deg);

    Region operator|(const Region& other) const;  // union
    Region operator-(const Region& other) const;  // subtract
    Region operator&(const Region& other) const;  // intersect
    Region operator^(const Region& other) const;  // xor

    // The union of many regions at once. Regions whose bounding boxes do not
    // touch are only collected, without a boolean; only groups that touch
    // are merged by Open CASCADE. An array of separate shapes is cheap.
    static Region unite(std::span<const Region> regions);

    // Moves and rotations share the geometry (an Open CASCADE location);
    // mirrors and scaling copy it, which Open CASCADE requires.
    Region transformed(const Transform& t) const;

    bool empty() const;
    // Connected pieces (faces).
    int pieces() const;
    Box bbox() const;
    double area() const;  // µm²

    // The faces as polygons, curves split so that no point of a segment is
    // further than chord (µm) from the exact curve. Hulls are counter-
    // clockwise, holes clockwise.
    std::vector<Polygon> outlines(double chord) const;

    // The largest tolerance Open CASCADE carries on a vertex or edge of this
    // region, in µm: how far its geometry may be from exact.
    double max_tolerance() const;

    // Whether Open CASCADE's full check finds the region's faces valid (closed
    // boundaries, holes inside, nothing crossing). For tests and diagnostics:
    // it is not cheap.
    bool valid() const;

    struct Impl;

private:
    friend struct RegionAccess;
    explicit Region(std::shared_ptr<const Impl> impl);
    std::shared_ptr<const Impl> impl_;
};

}  // namespace mgeom
