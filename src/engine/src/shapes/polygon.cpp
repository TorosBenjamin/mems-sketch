// Closed polygon through its points (mems_sketch.core.shapes.kinds.polygon).
#include <vector>

#include "kinds.hpp"

namespace mems {

Result render_polygon(const Json& node, const Context& ctx) {
    std::vector<mgeom::Point> points;
    for (const auto& point : node.at("points")) points.push_back({ctx.value(point.at(0)), ctx.value(point.at(1))});
    const auto region = mgeom::Region::polygon(points);
    if (region.empty()) return {};
    return {Layers{{node.at("layer").get<std::string>(), region}}, {}};
}

}  // namespace mems
