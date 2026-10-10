// Boolean of two child lists, per layer (mems_sketch.core.shapes.kinds.boolean).
#include <set>

#include "kinds.hpp"

namespace mems {

Layers render_boolean(const Json& node, const Context& ctx) {
    const std::string op = node.at("op").get<std::string>();
    const Layers a = ctx.children(node.at("a")), b = ctx.children(node.at("b"));
    std::set<std::string> layers;
    for (const auto& [layer, region] : a) layers.insert(layer);
    for (const auto& [layer, region] : b) layers.insert(layer);
    Layers result;
    for (const auto& layer : layers) {
        const auto in_a = a.find(layer), in_b = b.find(layer);
        const mgeom::Region ra = in_a == a.end() ? mgeom::Region() : in_a->second;
        const mgeom::Region rb = in_b == b.end() ? mgeom::Region() : in_b->second;
        mgeom::Region out;
        if (op == "subtract") out = ra - rb;
        else if (op == "intersect") out = ra & rb;
        else if (op == "xor") out = ra ^ rb;
        else throw BuildError("unknown boolean operation '" + op + "'");
        if (!out.empty()) result.emplace(layer, std::move(out));
    }
    return result;
}

}  // namespace mems
