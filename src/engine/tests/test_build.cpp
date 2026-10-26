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
    "spring": {"name": "spring", "shapes": [{"kind": "ref", "component": "mems.comb_drive"}]},
    "hidden_anchor": {"name": "hidden_anchor", "shapes": [
      {"kind": "rect", "name": "anchor", "layer": "metal", "x0": 10, "y0": 0, "x1": 20, "y1": 10, "enabled": false},
      {"kind": "rect", "name": "comb", "layer": "device", "x0": 0, "y0": 0, "x1": 2, "y1": 2,
       "align": {"point": "center", "to": "anchor.center"}},
      {"kind": "rect", "name": "broken", "layer": "metal", "x0": 0, "y0": 0, "x1": "nothing", "y1": 1,
       "enabled": false}]}
  },
  "libraries": {"mems": {"comb_drive": {"name": "comb_drive",
                 "shapes": [{"kind": "rect", "layer": "level", "x0": 0, "y0": 0, "x1": 4, "y1": 2}]}}}
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

TEST_CASE("library components, and errors") {
    const Project project = Project::from_json(PROJECT);
    Builder builder(project);
    CHECK_THROWS_AS(builder.build("aligned"), mems::BuildError);  // aligned to a shape that is not there
    // A library's component drawn on the level of whatever places it: the stack's first.
    CHECK(builder.build("spring")->flat().at("device").area() == doctest::Approx(8.0));
    CHECK(builder.build("mems.comb_drive")->layers.count("device") == 1);
}

TEST_CASE("a switched-off shape is hidden, not removed: shapes aligned to it stay") {
    const Project project = Project::from_json(PROJECT);
    Builder builder(project);
    const auto& layers = builder.build("hidden_anchor")->flat();
    CHECK(layers.count("metal") == 0);  // neither the anchor nor the broken shape is drawn
    const auto box = layers.at("device").bbox();  // the comb, centred on the hidden anchor
    CHECK(box.x0 == doctest::Approx(14));
    CHECK(box.y0 == doctest::Approx(4));
}

TEST_CASE("ports turn with a placement, copies have theirs by index, and a port takes over one it is at") {
    const Project project = Project::from_json(R"({
      "name": "ports", "top": "top",
      "components": {
        "pad": {"name": "pad",
                "shapes": [{"kind": "rect", "name": "body", "layer": "metal", "x0": -10, "y0": -10, "x1": 10, "y1": 10}],
                "ports": [{"name": "signal", "at": "body.right", "direction": "right", "width": 4, "layer": "metal"}]},
        "top": {"name": "top",
                "shapes": [{"kind": "ref", "name": "p", "component": "pad", "x": 100, "rotation": 90,
                            "modifiers": [{"kind": "array", "columns": 2, "dx": 50}]},
                           {"kind": "rect", "name": "wire", "layer": "metal", "x0": 0, "y0": 0,
                            "x1": "p.port.signal.width", "y1": 1, "align": {"point": "bottom_left", "to": "p.port.signal[1]"}}],
                "ports": [{"name": "out", "at": "p.port.signal[1]", "width": 6},
                          {"name": "back", "at": "wire.right", "direction": "-90 - 90", "layer": "metal"}]}
      }
    })");
    Builder builder(project);
    const auto pad = builder.build("pad");
    REQUIRE(pad->ports.count("signal"));
    CHECK(pad->ports.at("signal").at.x == doctest::Approx(10));
    CHECK(pad->points.empty());  // a port is a point only where the component is placed

    const auto top = builder.build("top");
    const auto& out = top->ports.at("out");  // the second copy's, turned 90°: up
    CHECK(out.at.x == doctest::Approx(150));
    CHECK(out.at.y == doctest::Approx(10));
    CHECK(out.angle_deg == doctest::Approx(90));
    CHECK(out.width == doctest::Approx(6));  // its own; direction and layer taken over
    CHECK(out.layer == "metal");
    const auto& back = top->ports.at("back");  // -180 is 180
    CHECK(back.angle_deg == doctest::Approx(180));
    CHECK(back.at.x == doctest::Approx(154));  // the wire: 4 wide, from the port
    CHECK(back.width == 0);
}

TEST_CASE("a circle's segments are as given, from 3 to 4096, and refused otherwise") {
    const char* text = R"({
      "name": "s", "top": "tri",
      "components": {
        "tri": {"name": "tri", "parameters": [{"name": "n", "default": 3}],
                "shapes": [{"kind": "circle", "layer": "device", "radius": 1, "segments": "n"}]}}
    })";
    const Project project = Project::from_json(text);
    Builder builder(project);
    // A triangle in a unit circle: 3 √3 / 4; a hexagon: 3 √3 / 2 (not the octagon they gave before).
    CHECK(builder.build("tri")->flat().at("device").area() == doctest::Approx(3 * std::sqrt(3.0) / 4));
    CHECK(builder.build("tri", {{"n", 6.0}})->flat().at("device").area() ==
          doctest::Approx(3 * std::sqrt(3.0) / 2));
    for (const double n : {2.0, 4097.0, 6.5}) CHECK_THROWS_AS(builder.build("tri", {{"n", n}}), mems::BuildError);
}

TEST_CASE("the cache keeps the least recently used out, what was built ahead first") {
    const Project project = Project::from_json(PROJECT);
    auto cache = std::make_shared<mems::BuildCache>();
    Builder builder(project, cache);
    Builder ahead(project, cache);
    ahead.set_speculative(true);
    const auto real = builder.build("pad", {{"w", 3.0}});
    const size_t one = cache->bytes();
    CHECK(one > 0);
    CHECK(cache->speculative_bytes() == 0);
    ahead.build("pad", {{"w", 4.0}});
    ahead.build("pad", {{"w", 5.0}});
    CHECK(cache->speculative_bytes() > 0);
    // Asked for for real: kept like any other.
    const size_t before = cache->speculative_bytes();
    builder.build("pad", {{"w", 4.0}});
    CHECK(cache->speculative_bytes() < before);
    // No room for what was built ahead: it goes, the real ones stay.
    cache->set_limits(static_cast<size_t>(-1), 0);
    CHECK(cache->speculative_bytes() == 0);
    CHECK(builder.build("pad", {{"w", 3.0}}) == real);  // still there
    // Room for about one: the least recently used goes.
    cache->set_limits(cache->bytes() / 2, 0);
    CHECK(cache->bytes() <= one * 2);
}
