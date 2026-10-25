#include "kinds.hpp"

#include "mems/build.hpp"
#include "mgeom/transform.hpp"

#include <algorithm>
#include <map>
#include <string>

namespace mems {

RenderKind find_kind(std::string_view kind) {
    static const std::map<std::string, RenderKind, std::less<>> kinds = {
        {"rect", render_rect},
        {"polygon", render_polygon},
        {"circle", render_circle},
        {"boolean", render_boolean},
        {"transform", render_transform},
        {"group", render_transform},  // the earlier name of transform
        {"ref", render_ref},
        {"arc", render_arc},
        {"path", render_path},
        {"guide", render_guide},
        {"offset", render_offset},
        {"fillet", render_fillet},
        {"layer_map", render_layer_map},
        {"connection", render_connection},
        {"route", render_route},
    };
    const auto found = kinds.find(kind);
    return found == kinds.end() ? nullptr : found->second;
}

Result turned(Result made, const Json& node, const Context& ctx, mgeom::Point pivot) {
    const double angle = ctx.number(node, "rotation", 0.0);
    if (angle == 0.0) return made;
    const mgeom::Transform turn = mgeom::Transform::translation(pivot.x, pivot.y) * mgeom::Transform::rotation(angle) *
                                  mgeom::Transform::translation(-pivot.x, -pivot.y);
    for (auto& [layer, region] : made.layers) region = region.transformed(turn);
    made.instances = placed(made.instances, turn);
    for (auto& [name, point] : made.points) point = turn.apply(point);
    for (auto& [name, port] : made.ports) port = placed(port, turn);
    return made;
}

mgeom::Point centre_of(const std::vector<mgeom::Point>& points) {
    if (points.empty()) return {0.0, 0.0};
    double x0 = points.front().x, x1 = x0, y0 = points.front().y, y1 = y0;
    for (const auto& p : points) {
        x0 = std::min(x0, p.x);
        x1 = std::max(x1, p.x);
        y0 = std::min(y0, p.y);
        y1 = std::max(y1, p.y);
    }
    return {(x0 + x1) / 2, (y0 + y1) / 2};
}

}  // namespace mems
