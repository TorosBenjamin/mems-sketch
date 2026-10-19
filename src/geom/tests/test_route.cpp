#include <doctest/doctest.h>

#include <cmath>
#include <numbers>
#include <string>
#include <vector>

#include "mgeom/region.hpp"
#include "mgeom/route.hpp"

using namespace mgeom;

namespace {

constexpr double pi = std::numbers::pi;

auto approx(double v) { return doctest::Approx(v).epsilon(1e-9); }

// The points the wire goes through: its start and each segment's end.
std::vector<Point> through(const Wire& wire) {
    std::vector<Point> points{wire.start()};
    for (const auto& segment : wire.segments()) points.push_back(segment.end);
    return points;
}

void check_points(const Wire& wire, const std::vector<Point>& expected) {
    const auto points = through(wire);
    REQUIRE(points.size() == expected.size());
    for (size_t k = 0; k < points.size(); ++k) {
        CHECK(points[k].x == doctest::Approx(expected[k].x).scale(1));
        CHECK(points[k].y == doctest::Approx(expected[k].y).scale(1));
    }
}

Wire manhattan(RouteEnd start, RouteEnd end, std::vector<Point> via = {}, Bend bend = Bend::sharp, double radius = 0,
               double width = 4) {
    return route(start, end, via, RouteStyle::manhattan, bend, radius, width);
}

std::string message(RouteEnd start, RouteEnd end, std::vector<Point> via, RouteStyle style, Bend bend = Bend::sharp,
                    double radius = 0, double width = 4) {
    try {
        route(start, end, via, style, bend, radius, width);
    } catch (const GeometryError& e) {
        return e.what();
    }
    return "";
}

}  // namespace

TEST_CASE("straight routes") {
    SUBCASE("port to port, through via points") {
        check_points(route({{0, 0}, 0}, {{30, 40}, 90}, {}, RouteStyle::straight, Bend::sharp, 0, 2),
                     {{0, 0}, {30, 40}});
        check_points(route({{0, 0}, 0}, {{50, 50}, 90}, std::vector<Point>{{50, 0}}, RouteStyle::straight, Bend::sharp,
                           0, 2),
                     {{0, 0}, {50, 0}, {50, 50}});
    }
    SUBCASE("a via point it goes straight through is not a bend") {
        const auto wire = route({{0, 0}, 0}, {{100, 0}, 180}, std::vector<Point>{{50, 0}}, RouteStyle::straight,
                                Bend::round, 10, 2);
        CHECK(wire.segments().size() == 1);
    }
    SUBCASE("a round bend is an exact arc: the area is the sides' and a quarter ring's") {
        const double r = 10, w = 4;
        const auto wire =
            route({{0, 0}, 0}, {{50, 50}, 90}, std::vector<Point>{{50, 0}}, RouteStyle::straight, Bend::round, r, w);
        check_points(wire, {{0, 0}, {40, 0}, {50, 10}, {50, 50}});
        CHECK(wire.segments()[1].arc);
        CHECK(wire.segments()[1].ccw);  // a left turn
        const double sides = (40 + 40) * w, quarter_ring = pi / 4 * ((r + w / 2) * (r + w / 2) - (r - w / 2) * (r - w / 2));
        CHECK(Region::path(wire, w).area() == approx(sides + quarter_ring));
    }
    SUBCASE("a chamfer cuts the radius along each side, at any angle") {
        const auto wire = route({{0, 0}, 0}, {{50, -50}, 90}, std::vector<Point>{{50, 0}}, RouteStyle::straight,
                                Bend::chamfer, 5, 2);
        check_points(wire, {{0, 0}, {45, 0}, {50, -5}, {50, -50}});
    }
    SUBCASE("bends that do not fit say the largest that does") {
        // two right-angle bends 15 apart: a round radius of at most 7.5
        CHECK(message({{0, 0}, 0}, {{40, 15}, 180}, {{20, 0}, {20, 15}}, RouteStyle::straight, Bend::round, 10, 2) ==
              "bends of radius 10 do not fit between (20, 0) and (20, 15): the largest that fits is 7.5");
        CHECK(message({{0, 0}, 0}, {{20, 50}, 90}, {{4, 0}}, RouteStyle::straight, Bend::chamfer, 5, 2) ==
              "bends of radius 5 do not fit between (0, 0) and (4, 0): the largest that fits is 4");
    }
    SUBCASE("turning back on itself") {
        CHECK(message({{0, 0}, 0}, {{10, 0}, 0}, {{30, 0}}, RouteStyle::straight) ==
              "the route turns back on itself at (30, 0)");
    }
}

TEST_CASE("l routes") {
    SUBCASE("one bend, where the ports' lines meet") {
        check_points(route({{0, 0}, 0}, {{100, 50}, 270}, {}, RouteStyle::l, Bend::sharp, 0, 4),
                     {{0, 0}, {100, 0}, {100, 50}});
        // at any angle
        check_points(route({{0, 0}, 45}, {{20, 0}, 135}, {}, RouteStyle::l, Bend::sharp, 0, 1),
                     {{0, 0}, {10, 10}, {20, 0}});
    }
    SUBCASE("facing ports on one line: straight across") {
        check_points(route({{0, 0}, 0}, {{40, 0}, 180}, {}, RouteStyle::l, Bend::round, 5, 2), {{0, 0}, {40, 0}});
    }
    SUBCASE("what it cannot do") {
        CHECK(message({{0, 0}, 0}, {{40, 10}, 180}, {}, RouteStyle::l) ==
              "an l route needs its ports' lines to meet, and these are parallel: use manhattan");
        CHECK(message({{0, 0}, 0}, {{-50, 50}, 270}, {}, RouteStyle::l) ==
              "an l route bends where its ports' lines meet, and that is behind the start: use manhattan");
        CHECK(message({{0, 0}, 0}, {{50, 50}, 90}, {}, RouteStyle::l) ==
              "an l route bends where its ports' lines meet, and that is behind the end: use manhattan");
        CHECK(message({{0, 0}, 0}, {{50, 50}, 270}, {{10, 10}}, RouteStyle::l) ==
              "an l route has one bend and no via points");
        CHECK(message({{0, 0}, 0}, {{1, 50}, 270}, {}, RouteStyle::l) ==
              "the route bends 1 µm from a port, nearer than half its width (2 µm): it would cut back across the port");
    }
}

TEST_CASE("manhattan routes") {
    SUBCASE("facing on one line: straight") {
        check_points(manhattan({{0, 0}, 0}, {{100, 0}, 180}), {{0, 0}, {100, 0}});
    }
    SUBCASE("an L where the end is beside the start's line") {
        check_points(manhattan({{0, 0}, 0}, {{100, 50}, 270}), {{0, 0}, {100, 0}, {100, 50}});
    }
    SUBCASE("a Z bends halfway") {
        check_points(manhattan({{0, 0}, 0}, {{100, 20}, 180}), {{0, 0}, {50, 0}, {50, 20}, {100, 20}});
        check_points(manhattan({{0, 0}, 90}, {{-30, 80}, 270}), {{0, 0}, {0, 40}, {-30, 40}, {-30, 80}});
    }
    SUBCASE("a U between ports facing the same way, as near as its bends let it") {
        check_points(manhattan({{0, 0}, 0}, {{0, 40}, 0}), {{0, 0}, {4, 0}, {4, 40}, {0, 40}});  // the width
        const auto round = manhattan({{0, 0}, 0}, {{0, 40}, 0}, {}, Bend::round, 10);
        CHECK(through(round)[1].x == approx(10));  // the arc starts 10 before the corner at x = 20
        CHECK(through(round)[2].x == approx(20));
    }
    SUBCASE("round the back of the start") {
        // the end is behind the start and faces away: out, across, back, in
        const auto wire = manhattan({{0, 0}, 0}, {{-50, 20}, 180});
        check_points(wire, {{0, 0}, {4, 0}, {4, 10}, {-54, 10}, {-54, 20}, {-50, 20}});
    }
    SUBCASE("through via points") {
        const auto wire = manhattan({{0, 0}, 0}, {{100, 0}, 180}, {{50, 30}});
        const auto points = through(wire);
        bool on = false;  // (50, 30) is on one of the segments
        for (size_t k = 0; k + 1 < points.size(); ++k) {
            const Point a = points[k], b = points[k + 1];
            const bool between_x = std::min(a.x, b.x) - 1e-9 <= 50 && 50 <= std::max(a.x, b.x) + 1e-9;
            const bool between_y = std::min(a.y, b.y) - 1e-9 <= 30 && 30 <= std::max(a.y, b.y) + 1e-9;
            on = on || (between_x && between_y);
        }
        CHECK(on);
        for (size_t k = 0; k + 1 < points.size(); ++k)  // along x or y only
            CHECK((std::abs(points[k].x - points[k + 1].x) < 1e-9 || std::abs(points[k].y - points[k + 1].y) < 1e-9));
    }
    SUBCASE("bends too large for a Z go round instead") {
        // a Z would need 20 between its bends and has 5: it goes out and round
        const auto wire = manhattan({{0, 0}, 0}, {{100, 5}, 180}, {}, Bend::round, 10, 2);
        int arcs = 0;
        for (const auto& segment : wire.segments()) arcs += segment.arc;
        CHECK(arcs == 4);
        CHECK(Region::path(wire, 2).area() > 0);
    }
    SUBCASE("what it cannot do") {
        CHECK(message({{0, 0}, 30}, {{100, 0}, 180}, {}, RouteStyle::manhattan) ==
              "a manhattan route runs along x and y, and its start faces 30°: use straight or l");
        CHECK(message({{0, 0}, 0}, {{100, 0}, 200}, {}, RouteStyle::manhattan) ==
              "a manhattan route runs along x and y, and its end faces 200°: use straight or l");
        CHECK(message({{0, 0}, 0}, {{100, 0}, 180}, {}, RouteStyle::manhattan, Bend::round, 2, 4) ==
              "a round bend's radius must be more than half the width (2 µm)");
        CHECK(message({{0, 0}, 0}, {{0, 0}, 180}, {}, RouteStyle::manhattan) ==
              "a route's ports are at the same place (0, 0)");
    }
}
