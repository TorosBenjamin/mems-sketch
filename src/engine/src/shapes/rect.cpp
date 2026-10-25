// Rectangle between two corners, turned by its rotation about its centre
// (mems_sketch.core.shapes.kinds.rect).
#include "kinds.hpp"

namespace mems {

Result render_rect(const Json& node, const Context& ctx) {
    const double x0 = ctx.number(node, "x0"), y0 = ctx.number(node, "y0");
    const double x1 = ctx.number(node, "x1"), y1 = ctx.number(node, "y1");
    const auto region = mgeom::Region::rect(x0, y0, x1, y1);
    if (region.empty()) return {};
    return turned({Layers{{ctx.layer(node), region}}, {}}, node, ctx, {(x0 + x1) / 2, (y0 + y1) / 2});
}

}  // namespace mems
