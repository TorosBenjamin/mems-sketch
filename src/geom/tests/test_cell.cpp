#include <doctest/doctest.h>

#include <numbers>

#include "mgeom/cell.hpp"

using mgeom::ArraySpec;
using mgeom::Box;
using mgeom::Cell;
using mgeom::CellRef;
using mgeom::GeometryError;
using mgeom::PolarSpec;
using mgeom::Region;
using mgeom::Transform;

namespace {

constexpr double pi = std::numbers::pi;

CellRef square(double size, const char* layer = "device") {
    return Cell::Builder("square").add(layer, Region::rect(0, 0, size, size)).build();
}

void check_box(const Box& b, double x0, double y0, double x1, double y1) {
    CHECK(b.x0 == doctest::Approx(x0).epsilon(1e-12));
    CHECK(b.y0 == doctest::Approx(y0).epsilon(1e-12));
    CHECK(b.x1 == doctest::Approx(x1).epsilon(1e-12));
    CHECK(b.y1 == doctest::Approx(y1).epsilon(1e-12));
}

}  // namespace

TEST_CASE("a cell's own regions and layers") {
    const CellRef cell = Cell::Builder("pad")
                             .add("device", Region::rect(0, 0, 2, 2))
                             .add("device", Region::rect(1, 0, 3, 2))  // merged with the first
                             .add("metal", Region::circle({1, 1}, 0.5))
                             .build();
    CHECK(cell->name() == "pad");
    CHECK(cell->layers() == std::vector<std::string>{"device", "metal"});
    CHECK(cell->own("device").pieces() == 1);
    CHECK(cell->own("device").area() == doctest::Approx(6).epsilon(1e-12));
    CHECK(cell->own("oxide").empty());
    CHECK(cell->flat("device").area() == doctest::Approx(6).epsilon(1e-12));
    check_box(cell->bbox(), 0, 0, 3, 2);
}

TEST_CASE("placing a cell") {
    const CellRef sq = square(1);
    const CellRef parent = Cell::Builder("parent")
                               .place(sq, Transform::translation(10, 0))
                               .place(sq, Transform{0, 10, 90, false, 1})
                               .add("metal", Region::rect(0, 0, 1, 1))
                               .build();
    CHECK(parent->layers() == std::vector<std::string>{"device", "metal"});
    CHECK(parent->own("device").empty());
    CHECK(parent->placements().size() == 2);
    const Region& device = parent->flat("device");
    CHECK(device.pieces() == 2);
    check_box(device.bbox(), -1, 0, 11, 11);
    CHECK_THROWS_AS(Cell::Builder("bad").place(nullptr), GeometryError);
}

TEST_CASE("nested placements compose") {
    const CellRef sq = square(1);
    const CellRef row = Cell::Builder("row").place_array(sq, {}, ArraySpec{3, 1, 2, 0}).build();
    Transform mirror_and_turn{5, 5, 90, true, 1};
    const CellRef top = Cell::Builder("top").place(row, mirror_and_turn).build();
    // row covers x 0..5, y 0..1; mirrored y -1..0; turned 90°: x 0..1, y 0..5; moved by (5, 5).
    check_box(top->flat("device").bbox(), 5, 5, 6, 10);
    CHECK(top->flat("device").pieces() == 3);
    CHECK(top->flat("device").area() == doctest::Approx(3).epsilon(1e-12));
}

TEST_CASE("arrays") {
    SUBCASE("separate copies stay separate pieces") {
        const CellRef grid =
            Cell::Builder("grid").place_array(square(1), {}, ArraySpec{10, 10, 2, 3}).build();
        CHECK(grid->placements().size() == 100);
        const Region& device = grid->flat("device");
        CHECK(device.pieces() == 100);
        CHECK(device.area() == doctest::Approx(100).epsilon(1e-12));
        check_box(device.bbox(), 0, 0, 19, 28);
    }

    SUBCASE("overlapping and touching copies merge") {
        const CellRef bar =
            Cell::Builder("bar").place_array(square(2), {}, ArraySpec{5, 1, 1.5, 0}).build();
        CHECK(bar->flat("device").pieces() == 1);
        CHECK(bar->flat("device").area() == doctest::Approx(2 * 8).epsilon(1e-12));
        const CellRef touching =
            Cell::Builder("t").place_array(square(1), {}, ArraySpec{4, 1, 1, 0}).build();
        CHECK(touching->flat("device").pieces() == 1);
        REQUIRE(touching->flat("device").outlines(0.005).size() == 1);
        CHECK(touching->flat("device").outlines(0.005)[0].hull.size() == 4);
    }

    SUBCASE("an array of round holes cut from a plate") {
        const CellRef hole =
            Cell::Builder("hole").add("device", Region::circle({0, 0}, 1)).build();
        const CellRef holes = Cell::Builder("holes")
                                  .place_array(hole, Transform::translation(5, 5),
                                               ArraySpec{20, 20, 10, 10})
                                  .build();
        const Region plate = Region::rect(0, 0, 200, 200) - holes->flat("device");
        CHECK(plate.pieces() == 1);
        CHECK(plate.area() == doctest::Approx(200 * 200 - 400 * pi).epsilon(1e-12));
        CHECK(plate.outlines(0.005)[0].holes.size() == 400);
        CHECK(plate.valid());
    }

    CHECK_THROWS_AS(Cell::Builder("bad").place_array(square(1), {}, ArraySpec{0, 1, 1, 1}),
                    GeometryError);
}

TEST_CASE("polar arrays") {
    const CellRef bar = Cell::Builder("bar").add("device", Region::rect(10, -0.5, 14, 0.5)).build();

    SUBCASE("turning with the circle") {
        const CellRef ring =
            Cell::Builder("ring").place_polar(bar, {}, PolarSpec{4, {0, 0}, 0, true}).build();
        CHECK(ring->flat("device").pieces() == 4);
        check_box(ring->flat("device").bbox(), -14, -14, 14, 14);
        // The copy at 90° is upright.
        check_box(ring->placements()[1].cell->flat("device")
                      .transformed(ring->placements()[1].transform)
                      .bbox(),
                  -0.5, 10, 0.5, 14);
    }

    SUBCASE("keeping their orientation") {
        const CellRef ring =
            Cell::Builder("ring").place_polar(bar, {}, PolarSpec{4, {0, 0}, 0, false}).build();
        // The copy at 90°: its centre (12, 0) goes to (0, 12), still lying flat.
        check_box(ring->placements()[1].cell->flat("device")
                      .transformed(ring->placements()[1].transform)
                      .bbox(),
                  -2, 11.5, 2, 12.5);
    }

    SUBCASE("a step other than a full circle") {
        const CellRef fan =
            Cell::Builder("fan").place_polar(bar, {}, PolarSpec{3, {0, 0}, 30, true}).build();
        CHECK(fan->placements().size() == 3);
        CHECK(fan->placements()[2].transform.angle_deg == doctest::Approx(60));
    }
}

TEST_CASE("mirrored and scaled placements") {
    const CellRef disc = Cell::Builder("disc").add("device", Region::circle({3, 1}, 1)).build();
    Transform mirror;
    mirror.mirror_x = true;
    Transform scale;
    scale.scale = 2;
    const CellRef top = Cell::Builder("top").place(disc, mirror).place(disc, scale).build();
    const Region& device = top->flat("device");
    CHECK(device.pieces() == 2);
    CHECK(device.area() == doctest::Approx(pi + 4 * pi).epsilon(1e-12));
    check_box(device.bbox(), 2, -2, 8, 4);
}

TEST_CASE("what a cell flattens to is computed once") {
    const CellRef grid =
        Cell::Builder("grid").place_array(square(1), {}, ArraySpec{5, 5, 2, 2}).build();
    const Region* first = &grid->flat("device");
    const Region* second = &grid->flat("device");
    CHECK(first == second);
    CHECK(grid->flat("nothing").empty());
}

TEST_CASE("unite") {
    std::vector<Region> parts;
    for (int i = 0; i < 50; ++i) parts.push_back(Region::rect(i * 3, 0, i * 3 + 2, 1));
    // Overlaps the first ten, and touches the eleventh (x = 30): one piece of 11.
    parts.push_back(Region::rect(0, 0.5, 30, 0.7));
    const Region all = Region::unite(parts);
    CHECK(all.pieces() == 40);
    CHECK(all.area() == doctest::Approx(50 * 2 + 30 * 0.2 - 10 * 2 * 0.2).epsilon(1e-12));

    // The same as one union after another, and independent of the order.
    Region pairwise;
    for (const Region& r : parts) pairwise = pairwise | r;
    CHECK(pairwise.area() == doctest::Approx(all.area()).epsilon(1e-12));
    CHECK(pairwise.pieces() == all.pieces());
    std::vector<Region> reversed(parts.rbegin(), parts.rend());
    CHECK(Region::unite(reversed).area() == doctest::Approx(all.area()).epsilon(1e-12));
    CHECK(Region::unite(std::vector<Region>{}).empty());
}
