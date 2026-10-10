#include <doctest/doctest.h>

#include <cstdlib>

#include "mgeom/grid.hpp"

using namespace mgeom;

namespace {

GridPolygon box(std::int64_t l, std::int64_t b, std::int64_t r, std::int64_t t) {
    return {{{l, b}, {r, b}, {r, t}, {l, t}}, {}};
}

// Twice the area the polygons fill (holes taken off).
long long twice_area(const std::vector<GridPolygon>& polygons) {
    auto ring = [](const GridRing& r) {
        long long s = 0;
        for (size_t k = 0; k < r.size(); ++k) {
            const GridPoint &a = r[k], &b = r[(k + 1) % r.size()];
            s += a.x * b.y - b.x * a.y;
        }
        return std::llabs(s);
    };
    long long total = 0;
    for (const GridPolygon& p : polygons) {
        total += ring(p.hull);
        for (const GridRing& h : p.holes) total -= ring(h);
    }
    return total;
}

std::vector<GridPolygon> as_polygons(const std::vector<GridRing>& rings) {
    std::vector<GridPolygon> out;
    for (const GridRing& r : rings) out.push_back({r, {}});
    return out;
}

}  // namespace

TEST_CASE("booleans and offsets on the grid") {
    const auto two = grid::merged({box(0, 0, 10, 10), box(5, 5, 20, 20)});
    REQUIRE(two.size() == 1);
    CHECK(twice_area(two) == 2 * 300);
    const auto holed = grid::boolean({box(0, 0, 10, 10)}, {box(2, 2, 4, 4)}, grid::Op::subtract);
    REQUIRE(holed.size() == 1);
    CHECK(holed[0].holes.size() == 1);
    CHECK(twice_area(grid::offset({box(0, 0, 10, 10)}, 2, Join::miter)) == 2 * 196);
    CHECK(grid::offset({box(0, 0, 10, 10)}, -5, Join::miter).empty());
}

TEST_CASE("holes joined to their hull add no points and fill the same") {
    auto plate = grid::boolean({box(0, 0, 100, 100)},
                               {box(10, 10, 20, 20), box(50, 50, 60, 70), box(30, 10, 40, 20)},
                               grid::Op::subtract);
    REQUIRE(plate.size() == 1);
    REQUIRE(plate[0].holes.size() == 3);
    const auto rings = grid::hole_free(plate, 8000);
    REQUIRE(rings.size() == 1);
    CHECK(rings[0].size() == 4 + 3 * 4 + 3 * 2);  // each cut repeats two points
    const auto back = grid::merged(as_polygons(rings));
    REQUIRE(back.size() == 1);
    CHECK(back[0].holes.size() == 3);
    CHECK(twice_area(back) == twice_area(plate));

    SUBCASE("too many points: split first") {
        const auto pieces = grid::hole_free(plate, 12);
        CHECK(pieces.size() > 1);
        for (const GridRing& r : pieces) CHECK(r.size() <= 12);
        CHECK(twice_area(grid::merged(as_polygons(pieces))) == twice_area(plate));
    }
}
