// The project model (tests/test_engine_model.py compares it with Python's on
// the examples and generated projects).
#include <doctest/doctest.h>

#include "mems/expression.hpp"
#include "mems/project.hpp"

using mems::ModelError;
using mems::PrivateComponent;
using mems::Project;
using mems::UnknownComponent;

namespace {

const char* PROJECT = R"({
  "name": "demo", "top": "top",
  "process": {"constants": {"gap": 2, "pitch": "2 * gap + 1"}},
  "components": {
    "top": {"name": "top", "parameters": [{"name": "w", "default": 5}],
            "shapes": [{"kind": "ref", "component": "comb", "params": {}},
                       {"kind": "transform", "children": [{"kind": "ref", "component": "std.pad"}]}]},
    "comb": {"name": "comb",
             "parameters": [{"name": "fingers", "default": 4, "integer": true, "min": 1},
                            {"name": "width", "default": "process.gap * 2"},
                            {"name": "pitch", "default": "width + process.pitch", "internal": true}],
             "shapes": [{"kind": "ref", "component": "finger"}]},
    "comb/finger": {"name": "comb/finger", "shapes": []},
    "lonely": {"name": "lonely", "shapes": [{"kind": "ref", "component": "comb/finger"}]}
  },
  "libraries": {"std": {"pad": {"name": "pad", "shapes": [{"kind": "ref", "component": "anchor"}]}}},
  "imports": {"logo": {"digest": "abc", "cell": "LOGO", "layers": {"1/0": "metal"}}},
  "builtins": {"anchor": "v1", "comb_drive": "v1"}
})";

}  // namespace

TEST_CASE("process constants") {
    const Project p = Project::from_json(PROJECT);
    CHECK(p.scope() == std::map<std::string, double>{{"process.gap", 2}, {"process.pitch", 5}});
}

TEST_CASE("names are resolved from the inside out") {
    const Project p = Project::from_json(PROJECT);
    CHECK(p.qualify("finger", "comb") == "comb/finger");
    CHECK(p.qualify("finger", "comb/finger") == "comb/finger");
    CHECK(p.qualify("comb", "") == "comb");
    CHECK(p.qualify("anchor", "std.pad") == "anchor");  // a built-in
    CHECK(p.qualify("logo") == "logo");
    CHECK(p.qualify("std.pad", "") == "std.pad");
    CHECK(p.qualify("comb/finger") == "comb/finger");  // from outside: anything
    CHECK_THROWS_AS(p.qualify("finger", ""), UnknownComponent);
    CHECK_THROWS_AS(p.qualify("comb/finger", "top"), PrivateComponent);
    CHECK_THROWS_AS(p.qualify("nothing", ""), UnknownComponent);
    CHECK_THROWS_AS(p.qualify("std.nothing", ""), UnknownComponent);
}

TEST_CASE("references are checked") {
    CHECK_THROWS_WITH_AS(Project::from_json(PROJECT).check_references(),
                         "component 'lonely': 'comb/finger' is private to 'comb': it can only be placed inside 'comb'",
                         ModelError);
    const Project cycle = Project::from_json(R"({"components": {
        "a": {"name": "a", "shapes": [{"kind": "ref", "component": "b"}]},
        "b": {"name": "b", "shapes": [{"kind": "ref", "component": "a"}]}}})");
    CHECK_THROWS_WITH_AS(cycle.check_references(), "circular component reference: a -> b -> a", ModelError);
}

TEST_CASE("parameter values") {
    const Project p = Project::from_json(PROJECT);
    auto v = p.variables("comb");
    CHECK(v["fingers"] == 4);
    CHECK(v["width"] == 4);
    CHECK(v["pitch"] == 9);
    CHECK(v["process.gap"] == 2);
    v = p.variables("comb", {{"width", std::string("process.pitch")}, {"fingers", 2.0}});
    CHECK(v["width"] == 5);
    CHECK(v["pitch"] == 10);  // the default follows the given value
    CHECK_THROWS_AS(p.variables("comb", {{"fingers", 2.5}}), ModelError);  // an integer
    CHECK_THROWS_AS(p.variables("comb", {{"fingers", 0.0}}), ModelError);  // below its minimum
    CHECK_THROWS_AS(p.variables("comb", {{"nothing", 1.0}}), ModelError);
    CHECK_THROWS_AS(p.variables("comb", {{"width", std::string("w")}}), mems::ExpressionError);
    CHECK_THROWS_AS(p.variables("comb_drive"), ModelError);  // a built-in: Python's for now
}

TEST_CASE("fingerprints follow what a component depends on") {
    const Project p = Project::from_json(PROJECT);
    const std::string top = p.fingerprint("top"), comb = p.fingerprint("comb");
    CHECK(top.size() == 64);
    CHECK(top != comb);
    std::string edited = PROJECT;
    const std::string empty = R"("shapes": []})";
    edited.replace(edited.find(empty), empty.size(), R"("shapes": [{"kind": "rect"}]})");
    const Project q = Project::from_json(edited);
    CHECK(q.fingerprint("comb/finger") != p.fingerprint("comb/finger"));
    CHECK(q.fingerprint("comb") != comb);  // it places the finger
    CHECK(q.fingerprint("top") != top);
    CHECK(q.fingerprint("std.pad") == p.fingerprint("std.pad"));  // it does not
}

TEST_CASE("sha256") {
    CHECK(mems::sha256("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    CHECK(mems::sha256("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    CHECK(mems::sha256(std::string(1000, 'a')) == "41edece42d63e8d9bf515a9ba6932e1c20cbc9f5a5d134645adb5db1b9737ea3");
}
