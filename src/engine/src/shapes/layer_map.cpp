// The children's geometry moved between layers (mems_sketch.core.shapes.kinds.layer_map):
// mapped layers to their targets (several may merge), the others dropped
// unless keep_unmapped.
#include "kinds.hpp"

namespace mems {

Result render_layer_map(const Json& node, const Context& ctx) {
    const Json mapping = node.value("mapping", Json::object());
    if (mapping.empty()) throw BuildError("layer_map needs at least one mapping");
    const bool keep = node.value("keep_unmapped", false);
    // Both sides may be relative to the component's level; the first source wins.
    std::map<std::string, std::string> targets;
    for (const auto& [source, target] : mapping.items())
        targets.emplace(ctx.builder.layer(source), ctx.builder.layer(target.get<std::string>()));
    LayerSet result;
    for (const auto& [layer, region] : ctx.children(node.value("children", Json::array()))) {
        if (const auto found = targets.find(layer); found != targets.end()) result.add(found->second, region);
        else if (keep) result.add(layer, region);
    }
    return {result.merged(), {}};
}

}  // namespace mems
