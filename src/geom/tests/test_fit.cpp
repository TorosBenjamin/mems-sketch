#include <doctest/doctest.h>

#include <algorithm>
#include <cmath>
#include <numbers>
#include <vector>

#include "mgeom/fit.hpp"
#include "mgeom/measure.hpp"

using namespace mgeom;

namespace {

constexpr double pi = std::numbers::pi;

int count(const std::vector<bool>& fits) { return static_cast<int>(std::count(fits.begin(), fits.end(), true)); }

}  // namespace

TEST_CASE("a rounded rectangle has its corners rounded, the radius clamped") {
    const Region r = Region::rounded_rect(0, 0, 10, 4, 1);
    CHECK(r.area() == doctest::Approx(40 - (4 - pi)).epsilon(1e-9));
    CHECK(r.corners().empty());  // smooth all round
    CHECK(Region::rounded_rect(0, 0, 10, 4, 0).area() == doctest::Approx(40));
    CHECK(Region::rounded_rect(0, 0, 10, 4, -1).area() == doctest::Approx(40));
    // More than half the shorter side: a stadium, two half circles and two sides.
    const Region stadium = Region::rounded_rect(0, 0, 10, 4, 7);
    CHECK(stadium.area() == doctest::Approx(6 * 4 + pi * 4).epsilon(1e-9));
    CHECK(stadium.valid());
    const Region upright = Region::rounded_rect(0, 0, 4, 10, 2);
    CHECK(upright.area() == doctest::Approx(6 * 4 + pi * 4).epsilon(1e-9));
    CHECK(upright.bbox().y1 == doctest::Approx(10));
    CHECK(Region::rounded_rect(1, 1, 1, 5, 1).empty());
}

TEST_CASE("a fully rounded square is a circle: one edge") {
    const Region round = Region::rounded_rect(-3, -3, 3, 3, 3);
    const Region circle = Region::circle({0, 0}, 3);
    CHECK(round.area() == doctest::Approx(circle.area()).epsilon(1e-12));
    CHECK((round ^ circle).empty());
    CHECK(properties(round).perimeter == doctest::Approx(2 * pi * 3).epsilon(1e-12));
}

TEST_CASE("sites where a shape fits wholly inside an area") {
    const Region plate = Region::rect(0, 0, 10, 10);
    const Region hole = Region::circle({0, 0}, 1);
    const std::vector<Point> sites = {{5, 5}, {1.5, 5}, {1, 5}, {0.5, 5}, {-5, 5}, {9.5, 9.5}};
    const auto fits = fits_inside(plate, hole, sites);
    CHECK(fits == std::vector<bool>{true, true, false, false, false, false});  // touching does not fit
}

TEST_CASE("a shape does not fit over a hole of the area, nor around an island") {
    const Region plate = Region::rect(0, 0, 20, 20) - Region::circle({10, 10}, 3);
    const Region square = Region::rect(-1, -1, 1, 1);
    // In the hole, across its edge, clear of it.
    const std::vector<Point> sites = {{10, 10}, {12.5, 10}, {15, 10}};
    CHECK(fits_inside(plate, square, sites) == std::vector<bool>{false, false, true});
    // A big shape around the hole: the hole's ring is inside it.
    const Region big = Region::rect(-5, -5, 5, 5);
    CHECK(fits_inside(plate, big, std::vector<Point>{{10, 10}}) == std::vector<bool>{false});
}

TEST_CASE("fitting follows any outline: an L, and a ring with a curved edge") {
    const Point l[] = {{0, 0}, {20, 0}, {20, 6}, {6, 6}, {6, 20}, {0, 20}};
    const Region shape = Region::polygon(l);
    std::vector<Point> sites;
    for (int j = 0; j < 10; ++j)
        for (int i = 0; i < 10; ++i) sites.push_back({1 + 2.0 * i, 1 + 2.0 * j});
    const auto smaller = fits_inside(shape, Region::rect(-0.5, -0.5, 0.5, 0.5), sites);
    for (size_t k = 0; k < sites.size(); ++k) {
        const Point p = sites[k];
        CHECK(smaller[k] == (p.x < 6 || p.y < 6));
    }
    const Region ring = Region::circle({0, 0}, 10) - Region::circle({0, 0}, 4);
    const auto on_ring = fits_inside(ring, Region::circle({0, 0}, 1), std::vector<Point>{{7, 0}, {0, 0}, {9.5, 0}, {0, 4.9}, {0, 5.01}});
    CHECK(on_ring == std::vector<bool>{true, false, false, false, true});
}

TEST_CASE("thousands of sites") {
    const Region plate = Region::circle({0, 0}, 500);
    const Region hole = Region::rounded_rect(-2, -2, 2, 2, 1);
    std::vector<Point> sites;
    for (int j = -100; j <= 100; ++j)
        for (int i = -100; i <= 100; ++i) sites.push_back({5.0 * i, 5.0 * j});
    const auto fits = fits_inside(plate, hole, sites);
    // The hole's farthest point from the centre is on a corner arc.
    int surely = 0, maybe = 0;
    for (const Point& p : sites) {
        const double reach = std::hypot(std::abs(p.x) + 1, std::abs(p.y) + 1) + 1;
        surely += reach < 500 - 0.01;
        maybe += reach < 500 + 0.01;
    }
    CHECK(count(fits) >= surely);
    CHECK(count(fits) <= maybe);
    CHECK(surely > 30000);
}
