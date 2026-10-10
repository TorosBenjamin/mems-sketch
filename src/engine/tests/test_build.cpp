// Building the first shape kinds (tests/test_engine_build.py compares them
// with the Python backend's geometry).
#include <doctest/doctest.h>

#include <cmath>
#include <numbers>

#include "mems/build.hpp"

using mems::Builder;
using mems::NotSupported;
using mems::Project;

namespace {

const char* PROJECT = R"({
  "name": "demo", "top": "top",
  "process": {"constants": {"gap": 2}},
  "components": {
    "top": {"name": "top", "parameters": [{"name": "size", "default": 20}],
            "shapes": [
              {"kind": "boolean", "op": "subtract",
               "a": [{"kind": "rect", "layer": "device", "x0": 0, "y0": 0, "x1": "size", "y1": "size"}],
               "b": [{"kind": "circle", "layer": "device", "x": "2.5", "y": "2.5", "radius": 1,
                      "modifiers": [{"kind": "array", "columns": 4, "rows": 4, "dx": 5, "dy": 5}]}]},
              {"kind": "ref", "component": "pad", "x": 100, "params": {"w": "size / 2"}},
              {"kind": "rect", "layer": "metal", "x0": 0, "y0": 0, "x1": 1, "y1": 1, "enabled": false}]},
    "pad": {"name": "pad", "parameters": [{"name": "w", "default": 4}],
            "shapes": [{"kind": "transform", "rotation": 90,
                        "children": [{"kind": "polygon", "layer": "metal",
                                      "points": [[0, 0], ["w", 0], [0, "process.gap"]]}]}]},
    "aligned": {"name": "aligned", "shapes": [{"kind": "rect", "layer": "device", "x0": 0, "y0": 0,
                "x1": 1, "y1": 1, "align": {"point": "center", "to": "x.center"}}]},
    "spring": {"name": "spring", "shapes": [{"kind": "ref", "component": "comb_drive"}]}
  },
  "builtins": {"comb_drive": "v1"}
})";

}  // namespace

TEST_CASE("a plate with holes and a placed component") {
    const Project project = Project::from_json(PROJECT);
    Builder builder(project);
    const auto& layers = builder.build("top");
    REQUIRE(layers.size() == 2);  // the disabled metal rect adds nothing of its own
    CHECK(layers.at("device").area() == doctest::Approx(400 - 16 * std::numbers::pi).epsilon(1e-9));
    // The pad: a triangle 10 x 2, turned 90°, at x = 100.
    const auto& metal = layers.at("metal");
    CHECK(metal.area() == doctest::Approx(10.0));
    const auto box = metal.bbox();
    CHECK(box.x0 == doctest::Approx(98));
    CHECK(box.x1 == doctest::Approx(100));
    CHECK(box.y1 == doctest::Approx(10));
}

TEST_CASE("built once per fingerprint and values") {
    const Project project = Project::from_json(PROJECT);
    Builder builder(project);
    const auto* first = &builder.build("pad", {{"w", 3.0}});
    CHECK(&builder.build("pad", {{"w", 3.0}}) == first);
    CHECK(&builder.build("pad", {{"w", 5.0}}) != first);
}

TEST_CASE("what the engine does not build yet") {
    const Project project = Project::from_json(PROJECT);
    Builder builder(project);
    CHECK_THROWS_AS(builder.build("aligned"), NotSupported);
    CHECK_THROWS_AS(builder.build("spring"), NotSupported);
    CHECK_THROWS_AS(builder.build("comb_drive"), NotSupported);
}
