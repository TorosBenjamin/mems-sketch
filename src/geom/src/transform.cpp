#include "mgeom/transform.hpp"

#include <cmath>
#include <numbers>

namespace mgeom {

namespace {

double normalized_degrees(double a) {
    a = std::fmod(a, 360.0);
    return a < 0.0 ? a + 360.0 : a;
}

// cos and sin of a multiple of 90° are exact, so a quarter turn moves
// points to exact coordinates.
void cos_sin(double degrees, double& c, double& s) {
    const double a = normalized_degrees(degrees);
    if (a == 0.0) { c = 1.0; s = 0.0; }
    else if (a == 90.0) { c = 0.0; s = 1.0; }
    else if (a == 180.0) { c = -1.0; s = 0.0; }
    else if (a == 270.0) { c = 0.0; s = -1.0; }
    else {
        const double r = a * std::numbers::pi / 180.0;
        c = std::cos(r);
        s = std::sin(r);
    }
}

}  // namespace

Transform Transform::translation(double dx, double dy) {
    Transform t;
    t.dx = dx;
    t.dy = dy;
    return t;
}

Transform Transform::rotation(double angle_deg) {
    Transform t;
    t.angle_deg = angle_deg;
    return t;
}

Point Transform::apply(Point p) const {
    double c, s;
    cos_sin(angle_deg, c, s);
    const double y = mirror_x ? -p.y : p.y;
    const double x = p.x * scale;
    const double ys = y * scale;
    return {c * x - s * ys + dx, s * x + c * ys + dy};
}

Transform Transform::inverted() const {
    // p = R(a) S M q + d  =>  q = M S^-1 R(-a) (p - d)
    // and M R(-a) = R(a) M, so q = R(m ? a : -a) S^-1 M (p - d).
    Transform inv;
    inv.mirror_x = mirror_x;
    inv.scale = 1.0 / scale;
    inv.angle_deg = mirror_x ? angle_deg : -angle_deg;
    const Point moved = inv.apply({-dx, -dy});
    inv.dx = moved.x;
    inv.dy = moved.y;
    return inv;
}

bool Transform::is_identity() const {
    return dx == 0.0 && dy == 0.0 && normalized_degrees(angle_deg) == 0.0 && !mirror_x &&
           scale == 1.0;
}

Transform operator*(const Transform& a, const Transform& b) {
    // a(b(p)) = Ra Sa Ma (Rb Sb Mb p + db) + da
    // Ma Rb = R(-b) Ma when a mirrors, and mirrors cancel in pairs.
    Transform t;
    t.mirror_x = a.mirror_x != b.mirror_x;
    t.scale = a.scale * b.scale;
    t.angle_deg = a.angle_deg + (a.mirror_x ? -b.angle_deg : b.angle_deg);
    const Point moved = a.apply({b.dx, b.dy});
    t.dx = moved.x;
    t.dy = moved.y;
    return t;
}

}  // namespace mgeom
