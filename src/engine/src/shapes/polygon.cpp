// Closed polygon through its points, turned by its rotation about the centre of
// their box (mems_sketch.core.shapes.kinds.polygon).
#include <vector>

#include "kinds.hpp"

namespace mems {

Result render_polygon(const Json& node, const Context& ctx) {
    std::vector<mgeom::Point> points;
    for (const auto& point : node.at("points")) points.push_back({ctx.value(point.at(0)), ctx.value(point.at(1))});
    const auto region = mgeom::Region::polygon(points);
    if (region.empty()) return {};
    return turned({Layers{{ctx.layer(node), region}}, {}}, node, ctx, centre_of(points));
}

}  // namespace mems
