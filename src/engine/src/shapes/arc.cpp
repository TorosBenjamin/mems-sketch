// Annular sector, a ring when the angles span 360° (mems_sketch.core.shapes.kinds.arc).
// Exact; with a number of segments, the Python backend's polygon.
#include "arcs.hpp"
#include "kinds.hpp"

namespace mems {

Result render_arc(const Json& node, const Context& ctx) {
    const double cx = ctx.number(node, "x", 0.0), cy = ctx.number(node, "y", 0.0);
    const double r_in = ctx.number(node, "inner_radius", 0.0), r_out = ctx.number(node, "outer_radius");
    const double start = ctx.number(node, "start_angle", 0.0), end = ctx.number(node, "end_angle", 360.0);
    if (!(0 <= r_in && r_in < r_out)) throw BuildError("arc needs 0 <= inner_radius < outer_radius");
    if (end <= start) throw BuildError("arc end_angle must be greater than start_angle");
    const std::string layer = node.at("layer").get<std::string>();
    const double segments = explicit_segments(node, ctx);
    if (segments == 0.0 && r_out > ARC_TOLERANCE_UM) {
        const auto region = mgeom::Region::arc({cx, cy}, r_in, r_out, start, std::min(end, start + 360.0));
        return {Layers{{layer, region}}, {}};
    }
    const int n = segment_count(r_out, segments);
    auto pie = [&](double r) {
        auto points = arc_points(cx, cy, r, start, end, n);
        if (end - start < 360) points.push_back({cx, cy});
        return mgeom::Region::polygon(points);
    };
    const auto region = r_in > 0 ? pie(r_out) - pie(r_in) : pie(r_out);
    if (region.empty()) return {};
    return {Layers{{layer, region}}, {}};
}

}  // namespace mems
