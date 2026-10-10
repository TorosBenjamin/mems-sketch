// Circle (mems_sketch.core.shapes.kinds.circle). Exact, where the Python backend
// draws segments within 5 nm of it; with a number of segments it is that
// polygon, as there (at least 8, at most 4096), and so is a circle too small
// for arcs.
#include "arcs.hpp"
#include "kinds.hpp"

namespace mems {

Result render_circle(const Json& node, const Context& ctx) {
    const double r = ctx.number(node, "radius");
    const double cx = ctx.number(node, "x", 0.0), cy = ctx.number(node, "y", 0.0);
    const std::string layer = node.at("layer").get<std::string>();
    const double segments = explicit_segments(node, ctx);
    if (segments == 0.0 && r > ARC_TOLERANCE_UM) return {Layers{{layer, mgeom::Region::circle({cx, cy}, r)}}, {}};
    if (r == 0.0) return {};
    const auto region = mgeom::Region::polygon(arc_points(cx, cy, r, 0, 360, segment_count(r, segments)));
    if (region.empty()) return {};
    return {Layers{{layer, region}}, {}};
}

}  // namespace mems
