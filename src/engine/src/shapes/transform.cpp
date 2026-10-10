// Children mirrored about x, scaled, rotated and moved as one piece
// (mems_sketch.core.shapes.kinds.transform).
#include "kinds.hpp"
#include "mgeom/transform.hpp"

namespace mems {

Layers render_transform(const Json& node, const Context& ctx) {
    const double scale = ctx.number(node, "scale", 1.0);
    if (scale <= 0) throw BuildError("transform scale must be positive");
    const mgeom::Transform placement{ctx.number(node, "x", 0.0), ctx.number(node, "y", 0.0),
                                     ctx.number(node, "rotation", 0.0), node.value("mirror_x", false), scale};
    Layers result;
    for (auto& [layer, region] : ctx.children(node.value("children", Json::array())))
        result.emplace(layer, region.transformed(placement));
    return result;
}

}  // namespace mems
