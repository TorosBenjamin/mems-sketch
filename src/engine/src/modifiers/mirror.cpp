// The node and its mirror image: across the vertical line at x, the
// horizontal line at y or both, across a guide, or through a point
// (mems_sketch.core.shapes.modifiers.MirrorModifier).
#include <cmath>
#include <numbers>
#include <vector>

#include "modifiers.hpp"

namespace mems {

namespace {

// The point coordinates it needs besides its expressions' (about).
std::vector<std::string> references(const std::string& about) {
    if (about.empty()) return {};
    if (about.find('.') != std::string::npos) return {about + ".x", about + ".y"};
    return {about + ".start.x", about + ".start.y", about + ".end.x", about + ".end.y"};
}

}  // namespace

Result apply_mirror(const Json& modifier, const Produce& produce, const Variables& given, const ModifierContext& m) {
    const Result original = produce(given);
    const std::string about = modifier.contains("about") && modifier["about"].is_string()
                                  ? modifier["about"].get<std::string>()
                                  : std::string();
    const auto needed = references(about);
    const Variables variables = with_self(modifier, original, given, needed);
    const Context ctx{m.builder, m.component, variables, m.scope};

    std::vector<mgeom::Transform> images;
    auto mirrored = [](double dx, double dy, double angle) { return mgeom::Transform{dx, dy, angle, true, 1.0}; };
    if (about.empty()) {
        const double x0 = ctx.number(modifier, "x", 0.0), y0 = ctx.number(modifier, "y", 0.0);
        const mgeom::Transform flip_x = mirrored(2 * x0, 0, 180), flip_y = mirrored(0, 2 * y0, 0);
        const std::string axis = modifier.value("axis", std::string("x"));
        if (axis == "x") images = {flip_x};
        else if (axis == "y") images = {flip_y};
        else images = {flip_x, flip_y, flip_x * flip_y};
    } else {
        std::vector<double> values;
        for (const auto& name : needed) {
            const size_t dot = name.rfind('.');
            const mgeom::Point p = point_of(name.substr(0, dot), original, m.scope, "mirror about '" + about + "'");
            values.push_back(name.substr(dot + 1) == "x" ? p.x : p.y);
        }
        if (about.find('.') != std::string::npos) {  // point symmetry: turned 180° about the point
            images = {mgeom::Transform{2 * values[0], 2 * values[1], 180, false, 1.0}};
        } else {
            const double sx = values[0], sy = values[1], ex = values[2], ey = values[3];
            if (sx == ex && sy == ey) throw BuildError("guide '" + about + "' has no direction: its ends coincide");
            const double angle = std::atan2(ey - sy, ex - sx) * 180 / std::numbers::pi;
            images = {mgeom::Transform::translation(sx, sy) * mirrored(0, 0, 2 * angle) *
                      mgeom::Transform::translation(-sx, -sy)};
        }
    }
    LayerSet layers;
    Instances instances;
    if (modifier.value("keep", true)) {
        layers.add(original.layers);
        instances = original.instances;
    }
    for (const auto& image : images) {
        layers.add(placed(original.layers, image));
        for (auto& instance : placed(original.instances, image)) instances.push_back(std::move(instance));
    }
    return {layers.merged(), original.points, std::move(instances)};
}

}  // namespace mems
