// Building the first shape kinds (tests/test_engine_build.py compares them
// with the Python backend's geometry).
#include <doctest/doctest.h>

#include <cmath>
#include <numbers>

#include "mems/build.hpp"

using mems::Builder;
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
  "builtins": {"comb_drive": {"name": "comb_drive",
                 "shapes": [{"kind": "rect", "layer": "level", "x0": 0, "y0": 0, "x1": 4, "y1": 2}]}}
})";

}  // namespace

TEST_CASE("a plate with holes and a placed component") {
    const Project project = Project::from_json(PROJECT);
    Builder builder(project);
    const auto top = builder.build("top");
    const auto& layers = top->flat();
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
    const auto first = builder.build("pad", {{"w", 3.0}});
    CHECK(builder.build("pad", {{"w", 3.0}}) == first);
    CHECK(builder.build("pad", {{"w", 5.0}}) != first);
}

TEST_CASE("placed components stay instances of what was built") {
    const Project project = Project::from_json(PROJECT);
    Builder builder(project);
    const auto top = builder.build("top");
    CHECK(top->layers.count("metal") == 0);  // the pad's metal is the pad's own
    REQUIRE(top->instances.size() == 1);
    const mems::Instance& pad = top->instances.front();
    CHECK(pad.built == builder.build("pad", {{"w", 10.0}}));  // the same build, placed
    CHECK(pad.transform.dx == doctest::Approx(100));
    CHECK(top->box.x1 == doctest::Approx(100));
    CHECK(top->box.y1 == doctest::Approx(20));
}

TEST_CASE("operations flatten what they are given; modifiers copy instances") {
    const Project project = Project::from_json(R"({
      "name": "demo", "top": "row",
      "components": {
        "row": {"name": "row", "shapes": [
          {"kind": "ref", "component": "dot",
           "modifiers": [{"kind": "array", "columns": 3, "rows": 1, "dx": 10, "dy": 0},
                         {"kind": "mirror", "axis": "y", "y": -5}]},
          {"kind": "boolean", "op": "subtract",
           "a": [{"kind": "rect", "layer": "metal", "x0": 0, "y0": 20, "x1": 30, "y1": 30}],
           "b": [{"kind": "ref", "component": "dot", "x": 5, "y": 25}]}]},
        "dot": {"name": "dot", "shapes": [{"kind": "rect", "layer": "metal", "x0": -1, "y0": -1, "x1": 1, "y1": 1}]}
      }})");
    Builder builder(project);
    const auto row = builder.build("row");
    CHECK(row->instances.size() == 6);  // three copies, and their mirror images
    REQUIRE(row->layers.count("metal") == 1);  // the cut rect is its own geometry
    CHECK(row->layers.at("metal").area() == doctest::Approx(300 - 4));
    CHECK(row->flat().at("metal").area() == doctest::Approx(300 - 4 + 6 * 4));
}

TEST_CASE("built-in components, and errors") {
    const Project project = Project::from_json(PROJECT);
    Builder builder(project);
    CHECK_THROWS_AS(builder.build("aligned"), mems::BuildError);  // aligned to a shape that is not there
    // A built-in is built like any other component, on the default layer stack's first level.
    CHECK(builder.build("spring")->flat().at("device").area() == doctest::Approx(8.0));
    CHECK(builder.build("comb_drive")->layers.count("device") == 1);
}
