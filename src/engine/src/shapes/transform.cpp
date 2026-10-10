// Children mirrored about x, scaled, rotated and moved as one piece
// (mems_sketch.core.shapes.kinds.transform).
#include "kinds.hpp"
#include "mgeom/transform.hpp"

namespace mems {

Result render_transform(const Json& node, const Context& ctx) {
    const double scale = ctx.number(node, "scale", 1.0);
    if (scale <= 0) throw BuildError("transform scale must be positive");
    const mgeom::Transform placement{ctx.number(node, "x", 0.0), ctx.number(node, "y", 0.0),
                                     ctx.number(node, "rotation", 0.0), node.value("mirror_x", false), scale};
    // The children see the points around it in their own frame.
    Scope inner;
    const mgeom::Transform inverse = placement.inverted();
    for (const auto& [name, points] : ctx.scope) inner.emplace(name, points.seen_through(inverse));
    const Json children = node.value("children", Json::array());
    const Result parts = ctx.parts(children, &inner);  // what it places whole stays placed
    Layers result;
    for (const auto& [layer, region] : parts.layers) result.emplace(layer, region.transformed(placement));
    return {result, {}, placed(parts.instances, placement)};
}

}  // namespace mems
