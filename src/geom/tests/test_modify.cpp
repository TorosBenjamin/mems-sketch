#include <doctest/doctest.h>

#include <cmath>
#include <numbers>

#include "mgeom/measure.hpp"
#include "mgeom/region.hpp"

using namespace mgeom;

namespace {

constexpr double pi = std::numbers::pi;
constexpr double kPrecision = 1e-8;

auto approx(double v) { return doctest::Approx(v).epsilon(1e-9); }

Transform mirror_x() {
    Transform t;
    t.mirror_x = true;
    return t;
}

// An L: a 10 x 2 bar and a 2 x 8 leg standing on its left end. Five convex
// corners and one concave, at (2, 2).
Region l_shape() { return Region::rect(0, 0, 10, 2) | Region::rect(0, 2, 2, 10); }

}  // namespace

TEST_CASE("corners") {
    CHECK(Region::rect(0, 0, 4, 2).corners().size() == 4);
    for (const Corner& c : Region::rect(0, 0, 4, 2).corners()) {
        CHECK(c.convex);
        CHECK(std::abs(c.angle_deg) == approx(90));
    }
    const auto l = l_shape().corners();
    CHECK(l.size() == 6);
    int concave = 0;
    for (const Corner& c : l) {
        if (c.convex) continue;
        ++concave;
        CHECK(c.at.x == approx(2));
        CHECK(c.at.y == approx(2));
    }
    CHECK(concave == 1);
    CHECK(Region::circle({0, 0}, 1).corners().empty());

    SUBCASE("a hole's corners are concave for the material around it") {
        const Region plate = Region::rect(0, 0, 10, 10) - Region::rect(4, 4, 6, 6);
        int convex = 0, concave_count = 0;
        for (const Corner& c : plate.corners()) (c.convex ? convex : concave_count)++;
        CHECK(convex == 4);
        CHECK(concave_count == 4);
    }
    SUBCASE("mirroring does not change which corners are convex") {
        int concave_after = 0;
        for (const Corner& c : l_shape().transformed(mirror_x()).corners()) {
            if (!c.convex) {
                ++concave_after;
                CHECK(c.at.y == approx(-2));
            }
        }
        CHECK(concave_after == 1);
    }
    SUBCASE("a line running smoothly into an arc is not a corner") {
        // A slot: a rectangle with half-discs on its ends.
        const Region slot = Region::rect(0, 0, 10, 2) | Region::circle({0, 1}, 1) |
                            Region::circle({10, 1}, 1);
        CHECK(slot.corners().empty());
    }
}

TEST_CASE("offset") {
    const Region r = Region::rect(0, 0, 4, 2);

    SUBCASE("growing, with each join") {
        CHECK(r.offset(1, Join::miter).area() == approx(6 * 4));
        CHECK(r.offset(1, Join::round).area() == approx(8 + 2 * (4 + 2) + pi));
        CHECK(r.offset(1, Join::bevel).area() == approx(8 + 2 * (4 + 2) + 4 * 0.5));
        CHECK(r.offset(1, Join::round).corners().empty());  // arcs run smoothly
        CHECK(r.offset(1, Join::bevel).corners().size() == 8);
    }

    SUBCASE("shrinking") {
        CHECK(r.offset(-0.5).area() == approx(3 * 1));
        CHECK(r.offset(-0.5, Join::round).area() == approx(3 * 1));  // concave joins: no arcs
        CHECK(r.offset(-1.5).empty());                                // shrinks away
    }

    SUBCASE("circles stay exact") {
        const Region c = Region::circle({1, 1}, 2);
        CHECK(c.offset(0.5).area() == approx(pi * 2.5 * 2.5));
        CHECK(c.offset(-0.5).area() == approx(pi * 1.5 * 1.5));
    }

    SUBCASE("holes move the other way") {
        const Region plate = Region::rect(0, 0, 10, 10) - Region::circle({5, 5}, 2);
        const Region grown = plate.offset(1);
        CHECK(grown.area() == approx(12 * 12 - pi * 1));
        const Region shrunk = plate.offset(-1);
        CHECK(shrunk.area() == approx(8 * 8 - pi * 9));
        CHECK(plate.offset(2.5).outlines(0.005)[0].holes.empty());  // the hole closes
    }

    SUBCASE("a shrink can split a shape in two") {
        // A dumbbell: two 4 x 4 squares joined by a 1-wide neck.
        const Region bell = Region::rect(0, 0, 4, 4) | Region::rect(4, 1.5, 8, 2.5) |
                            Region::rect(8, 0, 12, 4);
        const Region split = bell.offset(-0.75);
        CHECK(split.pieces() == 2);
        CHECK(split.area() == approx(2 * 2.5 * 2.5));
    }

    SUBCASE("a grow can close a shape into a ring") {
        // A C: its gap of 1 (y 2 to 3, on the right) closes when it grows by 0.75.
        const Region c = Region::rect(0, 0, 10, 2) | Region::rect(0, 2, 2, 8) |
                         Region::rect(0, 8, 10, 10) | Region::rect(8, 3, 10, 8);
        CHECK(c.outlines(0.005)[0].holes.empty());
        const Region closed = c.offset(0.75);
        CHECK(closed.pieces() == 1);
        REQUIRE(closed.outlines(0.005).size() == 1);
        CHECK(closed.outlines(0.005)[0].holes.size() == 1);
    }

    SUBCASE("neighbours that grow into each other merge") {
        const Region two = Region::rect(0, 0, 1, 1) | Region::rect(2, 0, 3, 1);
        CHECK(two.pieces() == 2);
        CHECK(two.offset(0.6).pieces() == 1);
    }

    SUBCASE("an offset of 0 changes nothing, and tolerances stay small") {
        CHECK(r.offset(0).area() == approx(8));
        CHECK(l_shape().offset(0.3, Join::round).max_tolerance() < kPrecision);
        CHECK(l_shape().offset(0.3, Join::round).valid());
        CHECK(l_shape().transformed(mirror_x()).offset(0.5).area() ==
              approx(l_shape().offset(0.5).area()));
    }
}

TEST_CASE("fillet") {
    SUBCASE("convex corners") {
        const Region square = Region::rect(0, 0, 10, 10).filleted(1, 0);
        CHECK(square.area() == approx(100 - 4 * (1 - pi / 4)));
        CHECK(square.corners().empty());
        CHECK(square.valid());
    }
    SUBCASE("concave and convex radii apart") {
        const double l_area = 36;
        CHECK(l_shape().filleted(0, 1).area() == approx(l_area + (1 - pi / 4)));
        CHECK(l_shape().filleted(0.5, 0).area() == approx(l_area - 5 * 0.25 * (1 - pi / 4)));
        CHECK(l_shape().filleted(0.5, 1).area() ==
              approx(l_area + (1 - pi / 4) - 5 * 0.25 * (1 - pi / 4)));
    }
    SUBCASE("a mirrored shape is filleted the same") {
        CHECK(l_shape().transformed(mirror_x()).filleted(0.5, 1).area() ==
              approx(l_shape().filleted(0.5, 1).area()));
    }
    SUBCASE("the corners of holes") {
        const Region plate = Region::rect(0, 0, 10, 10) - Region::rect(4, 4, 6, 6);
        // The hole's corners are concave for the plate: they get the concave
        // radius, and rounding them adds material.
        CHECK(plate.filleted(0, 0.5).area() == approx(96 + 4 * 0.25 * (1 - pi / 4)));
    }
    SUBCASE("a radius that does not fit, and nothing to round") {
        CHECK_THROWS_AS(Region::rect(0, 0, 2, 2).filleted(1.5, 0), GeometryError);
        CHECK(Region::circle({0, 0}, 1).filleted(0.5, 0.5).area() == approx(pi));
        CHECK_THROWS_AS(Region::rect(0, 0, 1, 1).filleted(-1, 0), GeometryError);
    }
}

TEST_CASE("rounding and chamfering chosen corners") {
    const Region square = Region::rect(0, 0, 10, 10);
    const CornerRounding wanted[] = {
        {{10, 10}, 2, CornerStyle::round},
        {{0, 0}, 1, CornerStyle::chamfer},
    };
    const Region r = square.rounded(wanted);
    CHECK(r.area() == approx(100 - 4 * (1 - pi / 4) - 0.5));
    CHECK(r.corners().size() == 4);  // two untouched, two where the chamfer meets the edges
    CHECK(r.valid());

    SUBCASE("on a concave corner") {
        const CornerRounding inner[] = {{{2, 2}, 1, CornerStyle::round}};
        CHECK(l_shape().rounded(inner).area() == approx(36 + (1 - pi / 4)));
    }
    SUBCASE("errors name the corner") {
        const CornerRounding nowhere[] = {{{5, 5}, 1, CornerStyle::round}};
        CHECK_THROWS_AS(square.rounded(nowhere), GeometryError);
        const CornerRounding too_big[] = {{{0, 0}, 20, CornerStyle::round}};
        CHECK_THROWS_AS(square.rounded(too_big), GeometryError);
    }
}
