#include <doctest/doctest.h>

#include <cmath>
#include <numbers>

#include "mgeom/cell.hpp"
#include "mgeom/measure.hpp"

using namespace mgeom;

namespace {

constexpr double pi = std::numbers::pi;

auto approx(double v) { return doctest::Approx(v).epsilon(1e-10); }

}  // namespace

TEST_CASE("properties of a rectangle") {
    // 4 wide, 2 high, its lower left corner at (1, 3).
    const Properties p = properties(Region::rect(1, 3, 5, 5));
    CHECK(p.area == approx(8));
    CHECK(p.perimeter == approx(12));
    CHECK(p.centroid.x == approx(3));
    CHECK(p.centroid.y == approx(4));
    CHECK(p.ix == approx(4.0 * 8 / 12));   // b h³ / 12
    CHECK(p.iy == approx(2.0 * 64 / 12));  // h b³ / 12
    CHECK(std::abs(p.ixy) < 1e-9);
    CHECK(p.polar() == approx(p.ix + p.iy));
    CHECK(p.bbox.x0 == approx(1));
    CHECK(p.bbox.y1 == approx(5));
}

TEST_CASE("properties of round shapes are exact") {
    const Properties disc = properties(Region::circle({2, -1}, 3));
    CHECK(disc.area == approx(pi * 9));
    CHECK(disc.perimeter == approx(2 * pi * 3));
    CHECK(disc.centroid.x == approx(2));
    CHECK(disc.centroid.y == approx(-1));
    CHECK(disc.ix == approx(pi * 81 / 4));  // π r⁴ / 4
    CHECK(disc.iy == approx(pi * 81 / 4));

    const Properties ring = properties(Region::arc({0, 0}, 1, 2, 0, 360));
    CHECK(ring.area == approx(pi * 3));
    CHECK(ring.perimeter == approx(2 * pi * 3));  // both boundaries
    CHECK(ring.ix == approx(pi * (16 - 1) / 4));
}

TEST_CASE("the product of inertia of a turned rectangle") {
    // A 4 x 2 rectangle turned 45°: ∫xy = (h b³ - b h³) / 12 · sin 45° cos 45° = 4.
    const Region r = Region::rect(-2, -1, 2, 1).transformed(Transform::rotation(45));
    const Properties p = properties(r);
    CHECK(p.ixy == approx(4));
    // At 45° both are the mean of the upright ones: (8/3 + 32/3) / 2.
    CHECK(p.ix == approx(20.0 / 3));
    CHECK(p.iy == approx(20.0 / 3));
}

TEST_CASE("a centroid follows the material") {
    // An L: a 10 x 2 bar and a 2 x 8 leg standing on its left end.
    const Region l = Region::rect(0, 0, 10, 2) | Region::rect(0, 2, 2, 10);
    const Properties p = properties(l);
    CHECK(p.area == approx(36));
    CHECK(p.centroid.x == approx((20 * 5 + 16 * 1) / 36.0));
    CHECK(p.centroid.y == approx((20 * 1 + 16 * 6) / 36.0));
    CHECK(p.perimeter == approx(40));
}

TEST_CASE("mass properties of a proof mass") {
    // A 100 µm square of silicon (2329 kg/m³), 25 µm thick.
    const Properties p = properties(Region::rect(0, 0, 100, 100));
    const MassProperties m = mass_properties(p, 25, 2329);
    CHECK(m.volume == approx(100e-6 * 100e-6 * 25e-6));
    CHECK(m.mass == approx(2329 * 100e-6 * 100e-6 * 25e-6));
    // A square plate about its centre: m (a² + a²) / 12.
    CHECK(m.izz == approx(m.mass * 2 * 100e-6 * 100e-6 / 12));
    CHECK_THROWS_AS(mass_properties(p, -1, 2329), GeometryError);
}

TEST_CASE("distances") {
    SUBCASE("between rectangles, with the closest points") {
        const Distance d = distance(Region::rect(0, 0, 1, 1), Region::rect(4, 0.5, 5, 3));
        CHECK(d.value == approx(3));
        CHECK(d.a.x == approx(1));
        CHECK(d.b.x == approx(4));
        CHECK(d.a.y == d.b.y);
    }
    SUBCASE("between circles: exact") {
        const Distance d = distance(Region::circle({0, 0}, 1), Region::circle({3, 4}, 2));
        CHECK(d.value == approx(5 - 3));
    }
    SUBCASE("touching and overlapping regions are 0 apart") {
        CHECK(distance(Region::rect(0, 0, 1, 1), Region::rect(1, 0, 2, 1)).value == approx(0));
        CHECK(distance(Region::rect(0, 0, 2, 2), Region::rect(1, 1, 3, 3)).value == approx(0));
    }
    SUBCASE("a gap at the precision is measured") {
        const double gap = 2e-8;
        const Distance d = distance(Region::rect(0, 0, 1, 1), Region::rect(1 + gap, 0, 2, 1));
        CHECK(d.value == doctest::Approx(gap).epsilon(1e-3));
    }
    SUBCASE("the gap between comb fingers") {
        // Rotor fingers 2 wide and 7 apart, stator fingers between them: 1.5 gaps.
        const CellRef rotor_finger =
            Cell::Builder("r").add("device", Region::rect(0, 0, 2, 40)).build();
        const CellRef stator_finger =
            Cell::Builder("s").add("device", Region::rect(0, 0, 2, 40)).build();
        const CellRef rotor = Cell::Builder("rotor")
                                  .place_array(rotor_finger, {}, ArraySpec{5, 1, 7, 0})
                                  .build();
        const CellRef stator = Cell::Builder("stator")
                                   .place_array(stator_finger, Transform::translation(3.5, 25),
                                                ArraySpec{4, 1, 7, 0})
                                   .build();
        const Region& r = rotor->flat("device");
        const Region& s = stator->flat("device");
        CHECK(distance(r, s).value == approx(1.5));        // the gap between the fingers
        CHECK(projected_overlap(r, s, 90) == approx(15));  // engaged over 40 - 25
        CHECK(overlap_area(r, s) == approx(0));
    }
    CHECK_THROWS_AS(distance(Region(), Region::rect(0, 0, 1, 1)), GeometryError);
}

TEST_CASE("edges, for measuring and snapping") {
    SUBCASE("a rectangle has four lines") {
        const auto e = edges(Region::rect(0, 0, 4, 2));
        REQUIRE(e.size() == 4);
        double total = 0;
        for (const Edge& edge : e) {
            CHECK(edge.kind == EdgeKind::line);
            total += edge.length;
        }
        CHECK(total == approx(12));
    }
    SUBCASE("an arc knows its centre and radius") {
        const auto e = edges(Region::arc({1, 2}, 3, 5, 0, 90));
        int arcs = 0;
        for (const Edge& edge : e) {
            if (edge.kind != EdgeKind::arc) continue;
            ++arcs;
            CHECK(edge.centre.x == approx(1));
            CHECK(edge.centre.y == approx(2));
            CHECK((edge.radius == approx(3) || edge.radius == approx(5)));
            CHECK(edge.length == approx(edge.radius * pi / 2));
            // The midpoint is halfway round, at 45°.
            CHECK(std::hypot(edge.mid.x - 1, edge.mid.y - 2) == approx(edge.radius));
            CHECK(edge.mid.x - 1 == approx(edge.mid.y - 2));
        }
        CHECK(arcs == 2);
    }
    SUBCASE("edges follow the boundary: each starts where the last ended") {
        const auto e = edges(Region::rect(0, 0, 10, 10) - Region::circle({5, 5}, 2));
        REQUIRE(e.size() == 5);
        for (size_t i = 0; i < 4; ++i) {
            const Edge& next = e[(i + 1) % 4];
            CHECK(e[i].end.x == approx(next.start.x));
            CHECK(e[i].end.y == approx(next.start.y));
        }
        CHECK(e[4].kind == EdgeKind::arc);
    }
}
