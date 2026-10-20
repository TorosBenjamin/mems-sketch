// Copies on a grid; the copy's column and row are i and j
// (mems_sketch.core.shapes.modifiers.ArrayModifier). The first copy's points
// and ports are the node's; each copy's ports are also there by its index,
// counted along the rows (j * columns + i): drive[3].
#include "modifiers.hpp"

namespace mems {

Result apply_array(const Json& modifier, const Produce& produce, const Variables& given, const ModifierContext& m) {
    const Variables variables = uses_self(modifier) ? with_self(modifier, produce(given), given) : given;
    const Context ctx{m.builder, m.component, variables, m.scope};
    const int columns = copy_count(ctx, modifier, "columns", 1.0, "array columns");
    const int rows = copy_count(ctx, modifier, "rows", 1.0, "array rows");
    check_copies(static_cast<long long>(columns) * rows, "array columns × rows");
    const double dx = ctx.number(modifier, "dx", 0.0), dy = ctx.number(modifier, "dy", 0.0);
    LayerSet layers;
    Result result;
    for (int j = 0; j < rows; ++j) {
        for (int i = 0; i < columns; ++i) {
            Variables copy = variables;
            copy["i"] = i;
            copy["j"] = j;
            const Result made = produce(copy);
            const auto t = mgeom::Transform::translation(i * dx, j * dy);
            layers.add(placed(made.layers, t));
            for (auto& instance : placed(made.instances, t)) result.instances.push_back(std::move(instance));
            if (i == 0 && j == 0) {  // the first copy sits at the node's own place
                result.points.insert(made.points.begin(), made.points.end());
                result.ports.insert(made.ports.begin(), made.ports.end());
            }
            add_copy_ports(result, made, t, j * columns + i);
        }
    }
    result.layers = layers.merged();
    return result;
}

}  // namespace mems
