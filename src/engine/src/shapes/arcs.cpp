#include "arcs.hpp"

#include <algorithm>
#include <cmath>
#include <numbers>

namespace mems {

double explicit_segments(const Json& node, const Context& ctx) {
    const auto found = node.find("segments");
    if (found == node.end() || found->is_null()) return 0.0;
    if (found->is_number() && found->get<double>() == 0.0) return 0.0;
    return ctx.value(*found);
}

int segment_count(double radius, double explicit_segments) {
    int n;
    if (explicit_segments != 0.0) n = static_cast<int>(explicit_segments);
    else if (radius <= ARC_TOLERANCE_UM) n = 8;
    else n = static_cast<int>(std::ceil(std::numbers::pi / std::acos(1 - ARC_TOLERANCE_UM / radius)));
    return std::max(8, std::min(n, MAX_ARC_SEGMENTS));
}

std::vector<mgeom::Point> arc_points(double cx, double cy, double r, double start, double end, int n_full) {
    const double sweep = end - start;
    const int n = std::max(1, static_cast<int>(std::ceil(n_full * std::abs(sweep) / 360)));
    const bool full = std::abs(sweep) >= 360;
    const int count = full ? n : n + 1;
    std::vector<mgeom::Point> points;
    for (int k = 0; k < count; ++k) {
        const double angle = (start + sweep * k / n) * (std::numbers::pi / 180.0);
        points.push_back({cx + r * std::cos(angle), cy + r * std::sin(angle)});
    }
    return points;
}

}  // namespace mems
