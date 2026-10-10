// A construction line (mems_sketch.core.shapes.kinds.guide): no geometry, only
// its points start, end and center, to align to and measure from.
#include "kinds.hpp"

namespace mems {

Result render_guide(const Json& node, const Context& ctx) {
    const mgeom::Point start{ctx.number(node, "x0", 0.0), ctx.number(node, "y0", -50.0)};
    const mgeom::Point end{ctx.number(node, "x1", 0.0), ctx.number(node, "y1", 50.0)};
    if (start == end) {
        const std::string name = node.contains("name") && node["name"].is_string() ? node["name"].get<std::string>() : "guide";
        throw BuildError("guide '" + name + "' needs two different end points");
    }
    return {{}, PointMap{{"start", start}, {"end", end}, {"center", {(start.x + end.x) / 2, (start.y + end.y) / 2}}}};
}

}  // namespace mems
