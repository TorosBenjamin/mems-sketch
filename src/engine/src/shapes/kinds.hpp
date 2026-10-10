// The shape kinds the engine builds, one file each (src/shapes/<kind>.cpp),
// as mems_sketch.core.shapes.kinds has them. Nothing else switches on a kind.
#pragma once

#include <string_view>

#include "../eval/context.hpp"

namespace mems {

// One copy of a node: its geometry per layer.
using RenderKind = Layers (*)(const Json& node, const Context& ctx);

// The kind's renderer, or nothing when the engine does not build it yet.
RenderKind find_kind(std::string_view kind);

Layers render_rect(const Json& node, const Context& ctx);
Layers render_polygon(const Json& node, const Context& ctx);
Layers render_circle(const Json& node, const Context& ctx);
Layers render_boolean(const Json& node, const Context& ctx);
Layers render_transform(const Json& node, const Context& ctx);
Layers render_ref(const Json& node, const Context& ctx);

}  // namespace mems
