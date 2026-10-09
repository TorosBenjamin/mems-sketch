#pragma once

#include "mgeom/types.hpp"

namespace mgeom {

// A similarity transform: mirror about the x axis (y -> -y), then scale,
// then rotate counter-clockwise, then move. The same order as a placement in
// mems-sketch, and as KLayout's DCplxTrans.
//
// Transforms compose with *: (a * b).apply(p) == a.apply(b.apply(p)), so
// nested placements become one transform, applied once.
struct Transform {
    double dx = 0.0;
    double dy = 0.0;
    double angle_deg = 0.0;
    bool mirror_x = false;
    double scale = 1.0;

    static Transform translation(double dx, double dy);
    static Transform rotation(double angle_deg);

    Point apply(Point p) const;
    Transform inverted() const;
    bool is_identity() const;
    bool is_rigid() const { return scale == 1.0; }

    friend Transform operator*(const Transform& a, const Transform& b);
};

}  // namespace mgeom
