// Copies around a centre, step degrees apart, turning with the circle or
// keeping their orientation (mems_sketch.core.shapes.modifiers.PolarArrayModifier).
// The copy's index is i.
#include <optional>

#include "modifiers.hpp"

namespace mems {

Result apply_polar_array(const Json& modifier, const Produce& produce, const Variables& given,
                         const ModifierContext& m) {
    const Variables variables = uses_self(modifier) ? with_self(modifier, produce(given), given) : given;
    const Context ctx{m.builder, m.component, variables, m.scope};
    const int count = copy_count(ctx, modifier, "count", 4.0, "polar array count");
    const double step = modifier.contains("step") && !modifier["step"].is_null() ? ctx.value(modifier["step"])
                        : count ? 360.0 / count
                                : 0.0;
    const double cx = ctx.number(modifier, "x", 0.0), cy = ctx.number(modifier, "y", 0.0);
    const bool rotate = modifier.value("rotate", true);
    LayerSet layers;
    std::optional<PointMap> declared;
    for (int k = 0; k < count; ++k) {
        Variables copy = variables;
        copy["i"] = k;
        const Result made = produce(copy);
        const double angle = k * step;
        mgeom::Transform t;
        const mgeom::Transform turn{0.0, 0.0, angle, false, 1.0};
        if (rotate) {
            t = mgeom::Transform::translation(cx, cy) * turn * mgeom::Transform::translation(-cx, -cy);
        } else {  // the copy's centre goes round the circle; it keeps its orientation
            const mgeom::Box box = bbox_of(made.layers);
            const double px = box.empty() ? cx : (box.x0 + box.x1) / 2, py = box.empty() ? cy : (box.y0 + box.y1) / 2;
            const mgeom::Point turned = turn.apply({px - cx, py - cy});
            t = mgeom::Transform::translation(cx + turned.x - px, cy + turned.y - py);
        }
        layers.add(placed(made.layers, t));
        if (!declared) declared = made.points;
    }
    return {layers.merged(), declared.value_or(PointMap{})};
}

}  // namespace mems
