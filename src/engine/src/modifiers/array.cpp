// Copies on a grid; the copy's column and row are i and j
// (mems_sketch.core.shapes.modifiers.ArrayModifier).
#include <optional>

#include "modifiers.hpp"

namespace mems {

Result apply_array(const Json& modifier, const Produce& produce, const Variables& given, const ModifierContext& m) {
    const Variables variables = uses_self(modifier) ? with_self(modifier, produce(given), given) : given;
    const Context ctx{m.builder, m.component, variables, m.scope};
    const int columns = copy_count(ctx, modifier, "columns", 1.0, "array columns");
    const int rows = copy_count(ctx, modifier, "rows", 1.0, "array rows");
    const double dx = ctx.number(modifier, "dx", 0.0), dy = ctx.number(modifier, "dy", 0.0);
    LayerSet layers;
    std::optional<PointMap> declared;
    for (int j = 0; j < rows; ++j) {
        for (int i = 0; i < columns; ++i) {
            Variables copy = variables;
            copy["i"] = i;
            copy["j"] = j;
            const Result made = produce(copy);
            layers.add(placed(made.layers, mgeom::Transform::translation(i * dx, j * dy)));
            if (!declared) declared = made.points;  // the first copy sits at the node's own place
        }
    }
    return {layers.merged(), declared.value_or(PointMap{})};
}

}  // namespace mems
