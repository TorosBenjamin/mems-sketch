#include <doctest/doctest.h>

#include <cmath>
#include <numbers>

#include "mgeom/region.hpp"
#include "mgeom/snap.hpp"

using namespace mgeom;

namespace {

constexpr double nm = 0.001;  // the usual grid, in µm

int count(const Snapped& s, SnapChange change) {
    int n = 0;
    for (const SnapEvent& e : s.report.events) n += e.change == change;
    return n;
}

double twice_area(const GridRing& ring) {
    double a = 0;
    for (size_t k = 0; k < ring.size(); ++k) {
        const GridPoint p = ring[k], q = ring[(k + 1) % ring.size()];
        a += static_cast<double>(p.x) * static_cast<double>(q.y) -
             static_cast<double>(q.x) * static_cast<double>(p.y);
    }
    return a;
}

}  // namespace

TEST_CASE("snapping what is already on the grid changes nothing") {
    const Snapped s = snap(Region::rect(0, 0, 10, 5), nm, 0.005);
    REQUIRE(s.polygons.size() == 1);
    CHECK(s.polygons[0].hull.size() == 4);
    CHECK(s.polygons[0].holes.empty());
    CHECK(twice_area(s.polygons[0].hull) == 2 * 10000.0 * 5000.0);  // counter-clockwise
    CHECK(s.report.area_snapped == doctest::Approx(50).epsilon(1e-12));
    CHECK(s.report.area_exact == doctest::Approx(50).epsilon(1e-12));
    CHECK_FALSE(s.report.changed_shape());
    CHECK(s.grid == nm);
}

TEST_CASE("points round to the nearest grid point") {
    const Snapped s = snap(Region::rect(0.0004, 0, 1.0006, 1), nm, 0.005);
    REQUIRE(s.polygons.size() == 1);
    for (const GridPoint& p : s.polygons[0].hull) {
        CHECK((p.x == 0 || p.x == 1001));
    }
    CHECK(s.report.area_snapped == doctest::Approx(1.001).epsilon(1e-12));
    CHECK_FALSE(s.report.changed_shape());  // edges moved, nothing else
}

TEST_CASE("curves are split at the chord tolerance, with every point on the grid") {
    const Snapped s = snap(Region::circle({0, 0}, 10), nm, 0.005);
    REQUIRE(s.polygons.size() == 1);
    const GridRing& ring = s.polygons[0].hull;
    CHECK(ring.size() >= 100);  // π / acos(1 - 0.005 / 10) = 99.3 segments
    for (const GridPoint& p : ring) {
        const double r = std::hypot(static_cast<double>(p.x), static_cast<double>(p.y)) * nm;
        CHECK(r <= 10 + nm);
        CHECK(r >= 10 - 0.005 - nm);
    }
    CHECK(s.report.area_snapped == doctest::Approx(std::numbers::pi * 100).epsilon(1e-3));
    CHECK_FALSE(s.report.changed_shape());
}

TEST_CASE("holes stay holes, clockwise") {
    const Snapped s = snap(Region::rect(0, 0, 10, 10) - Region::rect(2, 2, 4, 4), nm, 0.005);
    REQUIRE(s.polygons.size() == 1);
    REQUIRE(s.polygons[0].holes.size() == 1);
    CHECK(twice_area(s.polygons[0].holes[0]) < 0);
    CHECK(s.report.area_snapped == doctest::Approx(96).epsilon(1e-12));
}

TEST_CASE("the report says what snapping changed") {
    SUBCASE("a piece smaller than the grid vanishes") {
        const Region r = Region::rect(0, 0, 1, 1) | Region::rect(5, 5, 5.0004, 5.0004);
        const Snapped s = snap(r, nm, 0.005);
        CHECK(s.polygons.size() == 1);
        CHECK(count(s, SnapChange::vanished) == 1);
        CHECK(s.report.events[0].where.x0 == doctest::Approx(5));
    }
    SUBCASE("a gap narrower than the grid closes") {
        const Region r = Region::rect(0, 0, 1, 1) | Region::rect(1.0004, 0, 2, 1);
        REQUIRE(r.pieces() == 2);
        const Snapped s = snap(r, nm, 0.005);
        CHECK(s.polygons.size() == 1);
        CHECK(count(s, SnapChange::merged) == 1);
        CHECK(s.report.events.size() == 1);
        CHECK(s.report.area_snapped == doctest::Approx(2).epsilon(1e-12));
    }
    SUBCASE("a neck narrower than the grid splits a piece") {
        const Point dumbbell[] = {{0, -1},      {1, -1},      {1, 0.0001}, {2, 0.0001},
                                  {2, -1},      {3, -1},      {3, 1},      {2, 1},
                                  {2, 0.0004}, {1, 0.0004}, {1, 1},      {0, 1}};
        const Snapped s = snap(Region::polygon(dumbbell), nm, 0.005);
        CHECK(s.polygons.size() == 2);
        CHECK(count(s, SnapChange::split) == 1);
    }
    SUBCASE("a hole smaller than the grid fills up") {
        const Region r = Region::rect(0, 0, 1, 1) - Region::rect(0.5001, 0.5001, 0.5003, 0.5003);
        const Snapped s = snap(r, nm, 0.005);
        REQUIRE(s.polygons.size() == 1);
        CHECK(s.polygons[0].holes.empty());
        CHECK(count(s, SnapChange::hole_closed) == 1);
        CHECK(s.report.events[0].where.x0 == doctest::Approx(0.5001));
    }
    SUBCASE("a wall thinner than the grid opens a hole to the outside") {
        const Region r = Region::rect(0, 0, 10, 10) - Region::rect(1, 1, 9.9996, 9);
        const Snapped s = snap(r, nm, 0.005);
        REQUIRE(s.polygons.size() == 1);
        CHECK(s.polygons[0].holes.empty());
        CHECK(count(s, SnapChange::hole_joined) == 1);
    }
    SUBCASE("a mouth narrower than the grid closes into a hole") {
        const Region c = Region::rect(0, 0, 10, 10) - Region::rect(1, 1, 9, 9) -
                         Region::rect(5, 8.5, 5.0004, 10.5);
        REQUIRE(c.pieces() == 1);
        const Snapped s = snap(c, nm, 0.005);
        REQUIRE(s.polygons.size() == 1);
        CHECK(s.polygons[0].holes.size() == 1);
        CHECK(count(s, SnapChange::hole_formed) == 1);
    }
    SUBCASE("an island inside a hole stays its own polygon") {
        const Region r = (Region::rect(0, 0, 10, 10) - Region::rect(2, 2, 8, 8)) |
                         Region::rect(4, 4, 6, 6);
        const Snapped s = snap(r, nm, 0.005);
        CHECK(s.polygons.size() == 2);
        CHECK_FALSE(s.report.changed_shape());
    }
}

TEST_CASE("a coarser grid") {
    const Snapped s = snap(Region::rect(0.02, 0, 1.04, 1), 0.05, 0.005);
    REQUIRE(s.polygons.size() == 1);
    for (const GridPoint& p : s.polygons[0].hull) CHECK((p.x == 0 || p.x == 21));
    CHECK(s.report.area_snapped == doctest::Approx(1.05).epsilon(1e-12));
}

TEST_CASE("snapping is deterministic") {
    const Region r = Region::circle({0.3333, 0.7777}, 5) - Region::circle({1, 1}, 1);
    const Snapped a = snap(r, nm, 0.005);
    const Snapped b = snap(r, nm, 0.005);
    REQUIRE(a.polygons.size() == b.polygons.size());
    CHECK(a.polygons[0].hull == b.polygons[0].hull);
    CHECK(a.polygons[0].holes == b.polygons[0].holes);
}

TEST_CASE("snapping errors") {
    const Region r = Region::rect(0, 0, 1, 1);
    CHECK_THROWS_AS(snap(r, 0, 0.005), GeometryError);
    CHECK_THROWS_AS(snap(r, -1, 0.005), GeometryError);
    CHECK_THROWS_AS(snap(r, nm, 0), GeometryError);
    CHECK_THROWS_AS(snap(Region::rect(0, 0, 1e9, 1), 1e-12, 0.005), GeometryError);
    CHECK(snap(Region(), nm, 0.005).polygons.empty());
}
