// The children's geometry moved between layers (mems_sketch.core.shapes.kinds.layer_map):
// mapped layers to their targets (several may merge), the others dropped
// unless keep_unmapped.
#include "kinds.hpp"

namespace mems {

Result render_layer_map(const Json& node, const Context& ctx) {
    const Json mapping = node.value("mapping", Json::object());
    if (mapping.empty()) throw BuildError("layer_map needs at least one mapping");
    const bool keep = node.value("keep_unmapped", false);
    LayerSet result;
    for (const auto& [layer, region] : ctx.children(node.value("children", Json::array()))) {
        if (mapping.contains(layer)) result.add(mapping[layer].get<std::string>(), region);
        else if (keep) result.add(layer, region);
    }
    return {result.merged(), {}};
}

}  // namespace mems
