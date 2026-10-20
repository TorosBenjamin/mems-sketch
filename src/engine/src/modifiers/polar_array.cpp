// Copies around a centre, step degrees apart, turning with the circle or
// keeping their orientation (mems_sketch.core.shapes.modifiers.PolarArrayModifier).
// The copy's index is i; its ports are there by it too: drive[2].
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
    if (count > 1 && step == 0.0) throw BuildError("polar array step must not be 0: every copy would be in one place");
    const double cx = ctx.number(modifier, "x", 0.0), cy = ctx.number(modifier, "y", 0.0);
    const bool rotate = modifier.value("rotate", true);
    LayerSet layers;
    Result result;
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
            const mgeom::Box box = bbox_of(made.layers, made.instances);
            const double px = box.empty() ? cx : (box.x0 + box.x1) / 2, py = box.empty() ? cy : (box.y0 + box.y1) / 2;
            const mgeom::Point turned = turn.apply({px - cx, py - cy});
            t = mgeom::Transform::translation(cx + turned.x - px, cy + turned.y - py);
        }
        layers.add(placed(made.layers, t));
        for (auto& instance : placed(made.instances, t)) result.instances.push_back(std::move(instance));
        if (k == 0) {
            result.points.insert(made.points.begin(), made.points.end());
            result.ports.insert(made.ports.begin(), made.ports.end());
        }
        add_copy_ports(result, made, t, k);
    }
    result.layers = layers.merged();
    return result;
}

}  // namespace mems
