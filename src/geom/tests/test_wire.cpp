#include <doctest/doctest.h>

#include <cmath>
#include <numbers>

#include "mgeom/measure.hpp"
#include "mgeom/region.hpp"
#include "mgeom/wire.hpp"

using namespace mgeom;

namespace {

constexpr double pi = std::numbers::pi;

auto approx(double v) { return doctest::Approx(v).epsilon(1e-9); }

void check_point(Point p, double x, double y) {
    CHECK(p.x == doctest::Approx(x).epsilon(1e-12).scale(1));
    CHECK(p.y == doctest::Approx(y).epsilon(1e-12).scale(1));
}

}  // namespace

TEST_CASE("building wires") {
    SUBCASE("arcs by radius, short and long, both ways") {
        Wire w({0, 0});
        w.arc_to({2, 0}, 1);  // a half circle: counter-clockwise, centre (1, 0)
        check_point(w.segments()[0].centre, 1, 0);
        CHECK(w.segments()[0].ccw);
        Wire small({0, 0});
        small.arc_to({2, 0}, 2);  // the shorter arc ccw: centre to the left of the chord
        CHECK(small.segments()[0].centre.y > 0);
        Wire large({0, 0});
        large.arc_to({2, 0}, 2, true);
        CHECK(large.segments()[0].centre.y < 0);
        CHECK_THROWS_AS(Wire({0, 0}).arc_to({5, 0}, 1), GeometryError);
    }
    SUBCASE("an arc through a point") {
        Wire w({1, 0});
        w.arc_through({0, 1}, {-1, 0});
        check_point(w.segments()[0].centre, 0, 0);
        CHECK(w.segments()[0].ccw);
        CHECK_THROWS_AS(Wire({0, 0}).arc_through({1, 1}, {2, 2}), GeometryError);
    }
    SUBCASE("a bulge of 1 is a half circle") {
        Wire w({0, 0});
        w.bulge_to({2, 0}, 1);
        check_point(w.segments()[0].centre, 1, 0);
        Wire straight({0, 0});
        straight.bulge_to({2, 0}, 0);
        CHECK_FALSE(straight.segments()[0].arc);
    }
    SUBCASE("turns and tangent arcs continue smoothly") {
        Wire w({0, 0});
        w.line_to({10, 0}).turn(5, 90);
        check_point(w.end(), 15, 5);
        check_point(w.end_direction(), 0, 1);
        w.turn(5, -90);
        check_point(w.end(), 20, 10);
        check_point(w.end_direction(), 1, 0);
        w.tangent_arc_to({20, 20});  // a half circle up and back
        check_point(w.segments().back().centre, 20, 15);
        check_point(w.end_direction(), -1, 0);
        CHECK_THROWS_AS(Wire({0, 0}).turn(1, 90), GeometryError);  // no direction yet
    }
}

TEST_CASE("polygons with arc segments") {
    SUBCASE("a slot: two lines and two half circles") {
        Wire slot({0, -1});
        slot.line_to({10, -1}).arc_to({10, 1}, 1).line_to({0, 1}).arc_to({0, -1}, 1);
        const Region r = Region::polygon(slot);
        CHECK(r.area() == approx(10 * 2 + pi));
        CHECK(r.corners().empty());
        CHECK(r.valid());
    }
    SUBCASE("closed automatically, with a bulge") {
        Wire d({0, 0});
        d.line_to({0, 4}).bulge_to({0, 0}, 1);  // a D bulging to the left
        const Region r = Region::polygon(d);
        CHECK(r.area() == approx(pi * 4 / 2));
        Wire open({0, 0});
        open.line_to({4, 0}).line_to({4, 3});  // closed back to the start
        CHECK(Region::polygon(open).area() == approx(6));
    }
    SUBCASE("an outline that crosses itself is refused") {
        Wire bow({0, 0});
        bow.line_to({2, 2}).line_to({2, 0}).line_to({0, 2});
        CHECK_THROWS_AS(Region::polygon(bow), GeometryError);
    }
}

TEST_CASE("paths") {
    SUBCASE("straight, with each end") {
        const Wire line = Wire({0, 0}).line_to({100, 0});
        CHECK(Region::path(line, 10).area() == approx(1000));
        CHECK(Region::path(line, 10, PathEnds::square).area() == approx(110 * 10));
        CHECK(Region::path(line, 10, PathEnds::round).area() == approx(1000 + pi * 25));
    }
    SUBCASE("a corner is mitered, as KLayout's paths are") {
        // KLayout: (0,-5) (0,5) (95,5) (95,60) (105,60) (105,-5): area 1600.
        const Wire l = Wire({0, 0}).line_to({100, 0}).line_to({100, 60});
        const Region r = Region::path(l, 10);
        CHECK(r.area() == approx(1600));
        CHECK(r.pieces() == 1);
        CHECK(r.outlines(0.005)[0].hull.size() == 6);
        // Sharper turns are cut where the outer edges have run on by half the
        // width (the same as KLayout's paths: areas from KLayout).
        const Wire turn135 = Wire({0, 0}).line_to({100, 0}).line_to({50, 50});
        CHECK(Region::path(turn135, 10).area() == doctest::Approx(1689.429112).epsilon(1e-9));
        const Wire sharp = Wire({0, 0}).line_to({100, 0}).line_to({0, 20});
        CHECK(Region::path(sharp, 10).area() == doctest::Approx(1816.842932).epsilon(1e-9));
        CHECK(Region::path(sharp, 10).valid());
    }
    SUBCASE("round and bevelled corners") {
        const Wire l = Wire({0, 0}).line_to({100, 0}).line_to({100, 60});
        // Without a join the two bands give 1575; the joins fill the outer 5 x 5 corner.
        CHECK(Region::path(l, 10, PathEnds::flush, Join::round).area() ==
              approx(1575 + pi * 25 / 4));
        CHECK(Region::path(l, 10, PathEnds::flush, Join::bevel).area() == approx(1575 + 12.5));
        CHECK(Region::path(l, 10, PathEnds::flush, Join::round).corners().size() == 5);
    }
    SUBCASE("a U-turn along an arc is exact and smooth") {
        const Wire u = Wire({0, 0}).line_to({50, 0}).turn(10, 180).line_to({0, 20});
        const Region r = Region::path(u, 2);
        CHECK(r.area() == approx(2 * 50 * 2 + pi * (11 * 11 - 9 * 9) / 2));
        CHECK(r.corners().size() == 4);  // only at the two flush ends
        CHECK(r.valid());
    }
    SUBCASE("a corner at an arc is rounded") {
        const Wire w = Wire({0, 0}).line_to({10, 0}).arc_to({20, 0}, 5);  // a kink into a half circle
        const Region r = Region::path(w, 2);
        CHECK(r.valid());
        CHECK(r.pieces() == 1);
    }
    SUBCASE("a path that crosses itself is still one valid region") {
        const Wire loop = Wire({0, 0}).line_to({10, 0}).line_to({10, 10}).line_to({5, 10}).line_to({5, -5});
        const Region r = Region::path(loop, 1);
        CHECK(r.valid());
        CHECK(r.pieces() == 1);
    }
    SUBCASE("errors") {
        CHECK_THROWS_AS(Region::path(Wire({0, 0}).line_to({1, 0}), 0), GeometryError);
        CHECK_THROWS_AS(Region::path(Wire({0, 0}).line_to({1, 0}).turn(1, 90), 4), GeometryError);
        CHECK_THROWS_AS(Region::path(Wire({0, 0}), 1), GeometryError);
    }
}
