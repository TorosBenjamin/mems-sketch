// A component's shapes as they were read: the JSON of its shape tree, which
// the evaluator walks (inside the engine only: the public headers do not
// depend on the JSON library).
#pragma once

#include <nlohmann/json.hpp>

#include "mems/project.hpp"

namespace mems {

struct ShapeTree {
    nlohmann::ordered_json json;    // a list of nodes
    nlohmann::ordered_json points;  // its declared points (PointDef), a list
};

}  // namespace mems
