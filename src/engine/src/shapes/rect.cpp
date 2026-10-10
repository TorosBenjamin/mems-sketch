// Rectangle between two corners (mems_sketch.core.shapes.kinds.rect).
#include "kinds.hpp"

namespace mems {

Result render_rect(const Json& node, const Context& ctx) {
    const auto region = mgeom::Region::rect(ctx.number(node, "x0"), ctx.number(node, "y0"), ctx.number(node, "x1"),
                                            ctx.number(node, "y1"));
    if (region.empty()) return {};
    return {Layers{{node.at("layer").get<std::string>(), region}}, {}};
}

}  // namespace mems
