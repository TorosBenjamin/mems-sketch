// Circle (mems_sketch.core.shapes.kinds.circle). Exact, where the Python backend
// draws segments within 5 nm of it; with a number of segments it is that
// polygon, as there (at least 8, at most 4096).
#include <algorithm>
#include <cmath>
#include <numbers>
#include <vector>

#include "kinds.hpp"

namespace mems {

namespace {

constexpr double ARC_TOLERANCE_UM = 0.005;  // what the Python backend calls a small circle
constexpr int MAX_ARC_SEGMENTS = 4096;

// The number of segments asked for, as Python reads it: none, or 0, is none.
double explicit_segments(const Json& node, const Context& ctx) {
    const auto found = node.find("segments");
    if (found == node.end() || found->is_null()) return 0.0;
    if (found->is_number() && found->get<double>() == 0.0) return 0.0;
    return ctx.value(*found);
}

}  // namespace

Result render_circle(const Json& node, const Context& ctx) {
    const double r = ctx.number(node, "radius");
    const double cx = ctx.number(node, "x", 0.0), cy = ctx.number(node, "y", 0.0);
    const std::string layer = node.at("layer").get<std::string>();
    const double segments = explicit_segments(node, ctx);
    if (segments == 0.0 && r > ARC_TOLERANCE_UM) return {Layers{{layer, mgeom::Region::circle({cx, cy}, r)}}, {}};
    // A polygon: as many segments as asked for, or 8 for a circle too small for arcs.
    const int n = segments != 0.0 ? std::max(8, std::min(static_cast<int>(segments), MAX_ARC_SEGMENTS)) : 8;
    if (r == 0.0) return {};
    std::vector<mgeom::Point> points;
    for (int k = 0; k < n; ++k) {
        const double angle = (360.0 * k / n) * (std::numbers::pi / 180.0);
        points.push_back({cx + r * std::cos(angle), cy + r * std::sin(angle)});
    }
    const auto region = mgeom::Region::polygon(points);
    if (region.empty()) return {};
    return {Layers{{layer, region}}, {}};
}

}  // namespace mems
