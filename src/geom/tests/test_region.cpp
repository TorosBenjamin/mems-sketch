#include <doctest/doctest.h>

#include <cmath>
#include <numbers>

#include "mgeom/region.hpp"

using mgeom::Box;
using mgeom::GeometryError;
using mgeom::Point;
using mgeom::Region;
using mgeom::Transform;

namespace {

constexpr double pi = std::numbers::pi;

// Requirement QP-1: the geometry's precision is 1e-8 µm or finer.
constexpr double kPrecision = 1e-8;

void check_box(const Box& b, double x0, double y0, double x1, double y1) {
    CHECK(b.x0 == doctest::Approx(x0).epsilon(1e-12));
    CHECK(b.y0 == doctest::Approx(y0).epsilon(1e-12));
    CHECK(b.x1 == doctest::Approx(x1).epsilon(1e-12));
    CHECK(b.y1 == doctest::Approx(y1).epsilon(1e-12));
}

}  // namespace

TEST_CASE("an empty region") {
    const Region r;
    CHECK(r.empty());
    CHECK(r.pieces() == 0);
    CHECK(r.area() == 0.0);
    CHECK(r.bbox().empty());
    CHECK(r.outlines(0.005).empty());
    CHECK((r | Region::rect(0, 0, 1, 1)).area() == doctest::Approx(1));
    CHECK((Region::rect(0, 0, 1, 1) - r).area() == doctest::Approx(1));
    CHECK((r & Region::rect(0, 0, 1, 1)).empty());
}

TEST_CASE("rectangles and polygons") {
    const Region r = Region::rect(2, 3, -1, 1);  // corners in any order
    CHECK(r.pieces() == 1);
    CHECK(r.area() == doctest::Approx(6).epsilon(1e-12));
    check_box(r.bbox(), -1, 1, 2, 3);
    CHECK(Region::rect(0, 0, 0, 5).empty());

    const Point triangle[] = {{0, 0}, {4, 0}, {0, 3}};
    CHECK(Region::polygon(triangle).area() == doctest::Approx(6).epsilon(1e-12));

    const Point two[] = {{0, 0}, {1, 1}};
    CHECK_THROWS_AS(Region::polygon(two), GeometryError);
    const Point bow_tie[] = {{0, 0}, {2, 2}, {2, 0}, {0, 2}};
    CHECK_THROWS_AS(Region::polygon(bow_tie), GeometryError);
}

TEST_CASE("circles are exact, not polygons") {
    const Region c = Region::circle({1, 2}, 3);
    // An exact circle: the area is pi r² to the precision of the arithmetic,
    // where a 5 nm chord polygon would be off by about 1e-5 relative.
    CHECK(c.area() == doctest::Approx(pi * 9).epsilon(1e-12));
    check_box(c.bbox(), -2, -1, 4, 5);

    // The outline is split only when asked, to the chord asked for.
    const auto coarse = c.outlines(0.01);
    const auto fine = c.outlines(0.0001);
    REQUIRE(coarse.size() == 1);
    REQUIRE(fine.size() == 1);
    CHECK(fine[0].hull.size() > coarse[0].hull.size() * 5);
    for (const Point& p : fine[0].hull) {
        CHECK(std::hypot(p.x - 1, p.y - 2) == doctest::Approx(3).epsilon(1e-12));
    }
    CHECK_THROWS_AS(Region::circle({0, 0}, 0), GeometryError);
}

TEST_CASE("arcs: sectors, rings and pie slices") {
    const Region ring = Region::arc({0, 0}, 1, 2, 0, 360);
    CHECK(ring.pieces() == 1);
    CHECK(ring.area() == doctest::Approx(pi * 3).epsilon(1e-12));
    REQUIRE(ring.outlines(0.001).size() == 1);
    CHECK(ring.outlines(0.001)[0].holes.size() == 1);

    const Region quarter = Region::arc({0, 0}, 1, 2, 0, 90);
    CHECK(quarter.area() == doctest::Approx(pi * 3 / 4).epsilon(1e-12));
    check_box(quarter.bbox(), 0, 0, 2, 2);

    const Region pie = Region::arc({0, 0}, 0, 2, 90, 180);
    CHECK(pie.area() == doctest::Approx(pi).epsilon(1e-12));

    CHECK_THROWS_AS(Region::arc({0, 0}, 2, 1, 0, 90), GeometryError);
    CHECK_THROWS_AS(Region::arc({0, 0}, 1, 2, 90, 90), GeometryError);
}

TEST_CASE("booleans") {
    const Region a = Region::rect(0, 0, 2, 2);
    const Region b = Region::rect(1, 1, 3, 3);
    CHECK((a | b).pieces() == 1);
    CHECK((a | b).area() == doctest::Approx(7).epsilon(1e-12));
    CHECK((a - b).area() == doctest::Approx(3).epsilon(1e-12));
    CHECK((a & b).area() == doctest::Approx(1).epsilon(1e-12));
    CHECK((a ^ b).area() == doctest::Approx(6).epsilon(1e-12));
    CHECK((a ^ b).pieces() == 2);

    SUBCASE("a hole") {
        const Region plate = Region::rect(0, 0, 10, 10) - Region::rect(4, 4, 6, 6);
        CHECK(plate.pieces() == 1);
        const auto outlines = plate.outlines(0.005);
        REQUIRE(outlines.size() == 1);
        CHECK(outlines[0].hull.size() == 4);
        REQUIRE(outlines[0].holes.size() == 1);
        CHECK(outlines[0].holes[0].size() == 4);
    }

    SUBCASE("a round hole stays exact") {
        const Region plate = Region::rect(0, 0, 10, 10) - Region::circle({5, 5}, 2);
        CHECK(plate.area() == doctest::Approx(100 - 4 * pi).epsilon(1e-12));
    }

    SUBCASE("touching shapes merge, without internal edges") {
        const Region joined = Region::rect(0, 0, 1, 1) | Region::rect(1, 0, 2, 1);
        CHECK(joined.pieces() == 1);
        REQUIRE(joined.outlines(0.005).size() == 1);
        CHECK(joined.outlines(0.005)[0].hull.size() == 4);
    }

    SUBCASE("disjoint shapes stay separate pieces") {
        const Region two = Region::rect(0, 0, 1, 1) | Region::rect(5, 0, 6, 1);
        CHECK(two.pieces() == 2);
        CHECK(two.area() == doctest::Approx(2).epsilon(1e-12));
    }
}

TEST_CASE("the precision is 1e-8 µm or finer") {
    SUBCASE("a gap of twice the precision survives a union") {
        const double gap = 2 * kPrecision;
        const Region two = Region::rect(0, 0, 1, 1) | Region::rect(1 + gap, 0, 2, 1);
        CHECK(two.pieces() == 2);
        CHECK(two.bbox().x1 == doctest::Approx(2).epsilon(1e-15));
    }

    SUBCASE("a sliver of twice the precision survives a subtraction") {
        const double sliver = 2 * kPrecision;
        const Region left = Region::rect(0, 0, 1, 1) - Region::rect(sliver, -1, 2, 2);
        CHECK(left.pieces() == 1);
        CHECK(left.area() == doctest::Approx(sliver).epsilon(1e-6));
    }

    SUBCASE("tolerances stay below the precision through operations") {
        Region r = Region::rect(0, 0, 100, 100);
        for (int i = 0; i < 10; ++i) {
            for (int j = 0; j < 10; ++j) {
                r = r - Region::circle({5.0 + 10 * i, 5.0 + 10 * j}, 2.0 + 0.01 * i);
            }
        }
        r = r & Region::arc({50, 50}, 0, 60, 0, 360);
        r = r.transformed(Transform{1.5, -2.5, 33, true, 1});
        CHECK(r.max_tolerance() < kPrecision);
    }
}

TEST_CASE("transforms") {
    const Region r = Region::rect(0, 0, 4, 1);
    check_box(r.transformed(Transform::rotation(90)).bbox(), -1, 0, 0, 4);
    check_box(r.transformed(Transform::translation(10, -5)).bbox(), 10, -5, 14, -4);

    Transform mirror;
    mirror.mirror_x = true;
    const Region mirrored = r.transformed(mirror);
    check_box(mirrored.bbox(), 0, -1, 4, 0);
    CHECK(mirrored.area() == doctest::Approx(4).epsilon(1e-12));
    // A mirror reverses the faces; outlines still have counter-clockwise hulls.
    const auto hull = mirrored.outlines(0.005)[0].hull;
    double twice = 0;
    for (size_t i = 0; i < hull.size(); ++i) {
        const Point& a = hull[i];
        const Point& b = hull[(i + 1) % hull.size()];
        twice += a.x * b.y - b.x * a.y;
    }
    CHECK(twice > 0);

    Transform scale;
    scale.scale = 2.5;
    const Region circle = Region::circle({0, 0}, 1).transformed(scale);
    CHECK(circle.area() == doctest::Approx(pi * 6.25).epsilon(1e-12));

    // Composing first and applying once gives the same region.
    const Transform a{3, 4, 30, true, 1};
    const Transform b{-1, 2, 45, false, 2};
    const Box once = r.transformed(a * b).bbox();
    const Box twice_applied = r.transformed(b).transformed(a).bbox();
    check_box(once, twice_applied.x0, twice_applied.y0, twice_applied.x1, twice_applied.y1);

    Transform bad;
    bad.scale = 0;
    CHECK_THROWS_AS(r.transformed(bad), GeometryError);
}

TEST_CASE("the same operations give the same outlines") {
    auto build = [] {
        return (Region::rect(0, 0, 10, 10) - Region::circle({3, 3}, 1) |
                Region::arc({8, 8}, 1, 3, 10, 200))
            .outlines(0.002);
    };
    const auto first = build();
    const auto second = build();
    REQUIRE(first.size() == second.size());
    for (size_t i = 0; i < first.size(); ++i) {
        CHECK(first[i].hull == second[i].hull);
        CHECK(first[i].holes == second[i].holes);
    }
}

TEST_CASE("holes cut without a boolean give the same result as with one") {
    const Region plate = Region::rect(0, 0, 100, 100);

    SUBCASE("separate holes, square and round, some mirrored") {
        std::vector<Region> holes;
        for (int i = 0; i < 9; ++i) {
            for (int j = 0; j < 9; ++j) {
                const double x = 6 + 11 * i, y = 6 + 11 * j;
                Region h = (i + j) % 2 ? Region::circle({x, y}, 2 + 0.1 * i)
                                       : Region::rect(x - 2, y - 3, x + 2, y + 3);
                if (i % 3 == 0) {
                    Transform mirror;
                    mirror.mirror_x = true;
                    h = h.transformed(mirror).transformed(Transform::translation(0, 2 * y));
                }
                holes.push_back(h);
            }
        }
        const Region all_holes = Region::unite(holes);
        const Region cut = plate - all_holes;
        CHECK(cut.pieces() == 1);
        CHECK(cut.area() == doctest::Approx(plate.area() - all_holes.area()).epsilon(1e-12));
        REQUIRE(cut.outlines(0.005).size() == 1);
        CHECK(cut.outlines(0.005)[0].holes.size() == 81);
        CHECK(cut.valid());
        CHECK(cut.max_tolerance() < kPrecision);
    }

    SUBCASE("a hole touching the edge, one overlapping another, and a ring") {
        const Region notch = Region::rect(-1, 40, 5, 45);              // touches the edge
        const Region pair = Region::rect(20, 20, 30, 30) | Region::rect(28, 28, 35, 35);
        const Region ring = Region::arc({70, 70}, 3, 6, 0, 360);         // leaves an island
        const Region small = Region::circle({50, 80}, 2);              // the only easy one
        const Region cut = plate - Region::unite(std::vector<Region>{notch, pair, ring, small});
        CHECK(cut.valid());
        CHECK(cut.pieces() == 2);  // the plate, and the island inside the ring
        const double expected = 100 * 100 - 5 * 5 - (100 + 49 - 4) - pi * (36 - 9) - pi * 4;
        CHECK(cut.area() == doctest::Approx(expected).epsilon(1e-12));
    }

    SUBCASE("cutting again into a plate that has holes") {
        const Region once = plate - Region::circle({20, 20}, 5);
        const Region inside_old_hole = Region::circle({20, 20}, 1);  // removes nothing
        const Region twice = once - Region::unite(std::vector<Region>{
                                        inside_old_hole, Region::circle({60, 60}, 5)});
        CHECK(twice.area() == doctest::Approx(100 * 100 - 2 * pi * 25).epsilon(1e-12));
        CHECK(twice.outlines(0.005)[0].holes.size() == 2);
        CHECK(twice.valid());
    }
}
