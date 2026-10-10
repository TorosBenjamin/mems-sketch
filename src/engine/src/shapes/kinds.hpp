// The shape kinds the engine builds, one file each (src/shapes/<kind>.cpp),
// as mems_sketch.core.shapes.kinds has them. Nothing else switches on a kind.
#pragma once

#include <string_view>

#include "../eval/context.hpp"

namespace mems {

// One copy of a node: its geometry per layer, and the points it declares.
using RenderKind = Result (*)(const Json& node, const Context& ctx);

// The kind's renderer, or nothing when the engine does not build it yet.
RenderKind find_kind(std::string_view kind);

Result render_rect(const Json& node, const Context& ctx);
Result render_polygon(const Json& node, const Context& ctx);
Result render_circle(const Json& node, const Context& ctx);
Result render_boolean(const Json& node, const Context& ctx);
Result render_transform(const Json& node, const Context& ctx);
Result render_ref(const Json& node, const Context& ctx);

}  // namespace mems
