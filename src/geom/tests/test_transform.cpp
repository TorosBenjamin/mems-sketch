#include <doctest/doctest.h>

#include "mgeom/transform.hpp"

using mgeom::Point;
using mgeom::Transform;

namespace {

void check_same(Point a, Point b) {
    CHECK(a.x == doctest::Approx(b.x).epsilon(1e-12));
    CHECK(a.y == doctest::Approx(b.y).epsilon(1e-12));
}

Transform make(double dx, double dy, double angle, bool mirror, double scale) {
    Transform t;
    t.dx = dx;
    t.dy = dy;
    t.angle_deg = angle;
    t.mirror_x = mirror;
    t.scale = scale;
    return t;
}

const Point samples[] = {{0, 0}, {1, 0}, {0, 1}, {3.5, -2.25}, {-7, 11}};

}  // namespace

TEST_CASE("mirror, then scale, then rotate, then move") {
    const Transform t = make(10, 20, 90, true, 2);
    // (1, 2) -> mirror (1, -2) -> scale (2, -4) -> rotate 90° (4, 2) -> move (14, 22)
    check_same(t.apply({1, 2}), {14, 22});
}

TEST_CASE("quarter turns are exact") {
    const Transform t = Transform::rotation(90);
    const Point p = t.apply({1e-8, 3});
    CHECK(p.x == -3.0);
    CHECK(p.y == 1e-8);
    CHECK(Transform::rotation(-270).apply({1, 0}).y == 1.0);
}

TEST_CASE("composition applies the right transform first") {
    const Transform a = make(1, -2, 30, true, 1.5);
    const Transform b = make(-4, 5, 75, false, 0.5);
    const Transform c = make(0.25, 0, 200, true, 3);
    for (const Point& p : samples) {
        check_same((a * b).apply(p), a.apply(b.apply(p)));
        check_same((b * a).apply(p), b.apply(a.apply(p)));
        check_same((a * c).apply(p), a.apply(c.apply(p)));
        check_same(((a * b) * c).apply(p), (a * (b * c)).apply(p));
    }
}

TEST_CASE("the inverse undoes a transform") {
    for (bool mirror : {false, true}) {
        const Transform t = make(3, -1, 37, mirror, 2.5);
        for (const Point& p : samples) {
            check_same(t.inverted().apply(t.apply(p)), p);
            check_same(t.apply(t.inverted().apply(p)), p);
        }
    }
}

TEST_CASE("identity") {
    CHECK(Transform{}.is_identity());
    CHECK(Transform::rotation(360).is_identity());
    CHECK_FALSE(Transform::translation(0, 1e-9).is_identity());
    CHECK_FALSE(make(0, 0, 0, true, 1).is_identity());
}
