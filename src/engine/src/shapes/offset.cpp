// The children's outlines grown or shrunk (mems_sketch.core.shapes.kinds.offset).
#include "kinds.hpp"

namespace mems {

Result render_offset(const Json& node, const Context& ctx) {
    const double distance = ctx.number(node, "distance");
    const mgeom::Join join = node.value("corners", std::string("square")) == "bevel" ? mgeom::Join::bevel : mgeom::Join::miter;
    Layers result;
    for (const auto& [layer, region] : ctx.children(node.value("children", Json::array()))) {
        mgeom::Region out = region.offset(distance, join);
        if (!out.empty()) result.emplace(layer, std::move(out));
    }
    return {result, {}};
}

}  // namespace mems
