// Timings of the reference designs (requirement QS-2). Not a test: run
// build/geom/mgeom_bench and compare with the numbers in the requirements.
#include <chrono>
#include <cstdio>
#include <functional>

#include "mgeom/cell.hpp"

using namespace mgeom;

namespace {

template <class F>
auto timed(const char* label, F&& f) {
    const auto start = std::chrono::steady_clock::now();
    auto result = f();
    const std::chrono::duration<double> s = std::chrono::steady_clock::now() - start;
    std::printf("  %-52s %8.3f s\n", label, s.count());
    return result;
}

}  // namespace

int main() {
    std::setvbuf(stdout, nullptr, _IONBF, 0);  // show each timing as it comes
    for (int side : {32, 71, 100}) {  // about 1,000, 5,000 and 10,000 holes
        std::printf("\nPlate with %d release holes (4 um on a 10 um pitch)\n", side * side);
        for (bool round : {false, true}) {
            const CellRef hole =
                Cell::Builder("hole")
                    .add("device", round ? Region::circle({0, 0}, 2) : Region::rect(-2, -2, 2, 2))
                    .build();
            const CellRef holes = Cell::Builder("holes")
                                      .place_array(hole, Transform::translation(5, 5),
                                                   ArraySpec{side, side, 10, 10})
                                      .build();
            const Region& all = timed(round ? "flatten round holes (disjoint union)"
                                            : "flatten square holes (disjoint union)",
                                      [&]() -> const Region& { return holes->flat("device"); });
            const Region plate = Region::rect(0, 0, side * 10.0, side * 10.0);
            const Region cut = timed(round ? "plate minus round holes"
                                           : "plate minus square holes",
                                     [&] { return plate - all; });
            std::printf("  -> %d piece(s), %zu holes\n", cut.pieces(),
                        cut.outlines(0.005).front().holes.size());
        }
    }

    for (int fingers : {200, 1000}) {
        std::printf("\nComb, %d fingers on a backbone\n", fingers);
        const CellRef finger =
            Cell::Builder("finger").add("device", Region::rect(0, 0, 2, 52)).build();
        const CellRef comb = Cell::Builder("comb")
                                 .add("device", Region::rect(0, 0, fingers * 4.0, 10))
                                 .place_array(finger, Transform::translation(1, 8),
                                              ArraySpec{fingers, 1, 4, 0})
                                 .build();
        const Region& merged =
            timed("flatten (one group, merged by a boolean)",
                  [&]() -> const Region& { return comb->flat("device"); });
        std::printf("  -> %d piece(s)\n", merged.pieces());
    }
}
