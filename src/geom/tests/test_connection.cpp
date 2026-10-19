#include <doctest/doctest.h>

#include <cmath>
#include <numbers>
#include <string>
#include <vector>

#include "mgeom/connection.hpp"
#include "mgeom/region.hpp"

using namespace mgeom;

namespace {

constexpr double pi = std::numbers::pi;
constexpr double kReach = 0.5;
// What reaching into the beams adds: per end a trapezoid reach_in deep, a tenth
// of the width (at most reach_in) narrower at the back on each side.
constexpr double kBack = kReach * (10 - kReach);  // for width 10

auto approx(double v) { return doctest::Approx(v).epsilon(1e-9); }

ConnectionEnd end(double x, double y, double angle, double width = 10, std::string label = "") {
    return {{x, y}, angle, width, label.empty() ? std::to_string(int(x)) + "," + std::to_string(int(y)) : label};
}

// A T: beams from the left and the right, facing each other, and one from the top.
std::vector<ConnectionEnd> tee() { return {end(-20, 0, 0, 10, "left"), end(20, 0, 180, 10, "right"), end(0, 20, 270, 10, "top")}; }

std::string message(const std::vector<ConnectionEnd>& ends, double inner, double outer = 0) {
    try {
        connection(ends, inner, outer, kReach);
    } catch (const GeometryError& e) {
        return e.what();
    }
    return "";
}

}  // namespace

TEST_CASE("a T: sides that meet ahead make corners, collinear ones a straight edge") {
    const auto ends = tee();
    // The bar between left and right (40 x 10) and the stem up to the top end
    // (10 x 15), sharp where the stem meets the bar.
    const Region sharp = connection(ends, 0, 0, kReach);
    CHECK(sharp.valid());
    CHECK(sharp.pieces() == 1);
    CHECK(sharp.area() == approx(40 * 10 + 10 * 15 + 3 * kBack));
    // Rounded inner corners add a fillet's material at each.
    const Region rounded = connection(ends, 3, 0, kReach);
    CHECK(rounded.valid());
    CHECK(rounded.area() == approx(sharp.area() + 2 * 9 * (1 - pi / 4)));
    // The outer radius rounds nothing here: the bottom is straight.
    CHECK(connection(ends, 3, 4, kReach).area() == approx(rounded.area()));
}

TEST_CASE("an L: the inner corner and the outer one have their own radii") {
    const std::vector<ConnectionEnd> ends{end(20, 0, 180), end(0, 20, 270)};
    const double sharp = 25 * 25 - 15 * 15 + 2 * kBack;  // the square from (-5,-5) to the ends, less the empty corner
    CHECK(connection(ends, 0, 0, kReach).area() == approx(sharp));
    CHECK(connection(ends, 2, 0, kReach).area() == approx(sharp + 4 * (1 - pi / 4)));
    CHECK(connection(ends, 0, 4, kReach).area() == approx(sharp - 16 * (1 - pi / 4)));
}

TEST_CASE("a Y of three beams at 120°, rounded between each pair") {
    std::vector<ConnectionEnd> ends;
    for (int k = 0; k < 3; ++k) {
        const double a = 90 + 120 * k;  // from the centre out; the end points back in
        ends.push_back(end(30 * std::cos(a * pi / 180), 30 * std::sin(a * pi / 180), a + 180, 8, "e" + std::to_string(k)));
    }
    const Region sharp = connection(ends, 0, 0, kReach), rounded = connection(ends, 5, 0, kReach);
    CHECK(rounded.valid());
    CHECK(rounded.pieces() == 1);
    CHECK(rounded.area() > sharp.area());
    CHECK(rounded.corners().size() == 3 * 4);  // only where it reaches into the beams
}

TEST_CASE("joined to its beams, it is one piece with no corner where they meet") {
    const Region beams = Region::rect(-60, -5, -20, 5) | Region::rect(20, -5, 60, 5) | Region::rect(-5, 20, 5, 60);
    const Region joined = beams | connection(tee(), 3, 0, kReach);
    CHECK(joined.pieces() == 1);
    CHECK(joined.valid());
    for (const auto& c : joined.corners()) CHECK((std::abs(c.at.x) >= 59.9 || std::abs(c.at.y) >= 59.9 || c.at.y <= -4.9));
}

TEST_CASE("a radius too large for the room between two ends says how large it may be") {
    // In the T the corners are 15 from both ends' corners: 15 fits, 15.1 does not.
    CHECK(connection(tee(), 15, 0, kReach).valid());
    const std::string error = message(tee(), 15.1);
    CHECK(error.find("inner radius 15.1 does not fit between ends") != std::string::npos);
    CHECK(error.find("at most 15 there") != std::string::npos);
    CHECK(message({end(20, 0, 180), end(0, 20, 270)}, 0, 30).find("radius 30 does not fit") != std::string::npos);
}

TEST_CASE("sides that are almost parallel are joined straight, not by a far corner") {
    // Two beams from below, a little towards each other, and one from above:
    // their inner sides would only meet far above the junction.
    const std::vector<ConnectionEnd> fork{end(-10, -30, 89), end(10, -30, 91), end(0, 30, 270)};
    const Region r = connection(fork, 0, 0, kReach);
    CHECK(r.valid());
    CHECK(r.bbox().y1 <= 30 + kReach + 1e-9);  // no spike: only into the top beam
    CHECK(r.bbox().y0 >= -31);                 // and into the slanted ones
}

TEST_CASE("beams facing each other off line are joined by a straight-sided bridge") {
    const Region r = connection(std::vector<ConnectionEnd>{end(-20, 2, 0), end(20, -2, 180)}, 3, 3, kReach);
    CHECK(r.valid());
    CHECK(r.area() == approx(40 * 10 + 2 * kBack));  // a parallelogram: base 40, height 10
}

TEST_CASE("what a connection cannot be") {
    CHECK(message({end(0, 0, 0)}, 0) == "a connection joins at least two ends");
    CHECK(message({end(-20, 0, 0, 0, "a"), end(20, 0, 180)}, 0) == "end 'a' has no width: give it one");
    CHECK(message({end(-20, 0, 0, 10, "a"), end(-20, 0, 0, 10, "b"), end(20, 0, 180)}, 0) ==
          "ends 'a' and 'b' are the same");
    CHECK(message({end(-20, 0, 180, 10, "out"), end(20, 0, 180)}, 0) == "end 'out' points away from the others");
    // Ends whose edges cross: the outline would cross itself.
    CHECK(message({end(-1, 0, 0, 10, "a"), end(1, 0, 180, 10, "b"), end(0, -1, 90, 10, "c")}, 0).find("crosses itself") !=
          std::string::npos);
    CHECK_THROWS_AS(connection(tee(), -1, 0, kReach), GeometryError);
    CHECK_THROWS_AS(connection(tee(), 0, 0, 0), GeometryError);
}
