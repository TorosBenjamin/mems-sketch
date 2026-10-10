// The children's corners rounded: convex ones with radius, concave ones with
// inner_radius (mems_sketch.core.shapes.kinds.fillet).
#include "kinds.hpp"

namespace mems {

Result render_fillet(const Json& node, const Context& ctx) {
    const double r_out = ctx.number(node, "radius", 0.0), r_in = ctx.number(node, "inner_radius", 0.0);
    if (r_out < 0 || r_in < 0) throw BuildError("fillet radii must not be negative");
    Layers result;
    for (const auto& [layer, region] : ctx.children(node.value("children", Json::array()))) {
        mgeom::Region out = (r_out == 0 && r_in == 0) ? region : region.filleted(r_out, r_in);
        if (!out.empty()) result.emplace(layer, std::move(out));
    }
    return {result, {}};
}

}  // namespace mems
