// Rectangle between two corners, its corners rounded by corner_radius
// (mems_sketch.core.shapes.kinds.rect).
#include "kinds.hpp"

namespace mems {

Result render_rect(const Json& node, const Context& ctx) {
    const double radius = ctx.number(node, "corner_radius", 0.0);
    if (radius < 0) throw BuildError("corner radius must not be negative");
    const auto region = mgeom::Region::rounded_rect(ctx.number(node, "x0"), ctx.number(node, "y0"),
                                                    ctx.number(node, "x1"), ctx.number(node, "y1"), radius);
    if (region.empty()) return {};
    return {Layers{{ctx.layer(node), region}}, {}};
}

}  // namespace mems
