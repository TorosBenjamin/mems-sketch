// The modifiers the engine applies, one file each (src/modifiers/<kind>.cpp),
// as mems_sketch.core.shapes.modifiers has them. Each wraps what the node
// makes (``produce``, called with the copy's variables).
#pragma once

#include <functional>
#include <string_view>

#include "../eval/context.hpp"

namespace mems {

using Produce = std::function<Result(const Variables&)>;

struct ModifierContext {
    Builder& builder;
    const std::string& component;
    const Scope& scope;  // the named nodes the node can see
};

using ApplyModifier = Result (*)(const Json& modifier, const Produce& produce, const Variables& variables,
                                 const ModifierContext& ctx);

// The modifier's function, or nothing when the engine does not apply it yet.
ApplyModifier find_modifier(std::string_view kind);

Result apply_array(const Json& modifier, const Produce& produce, const Variables& variables, const ModifierContext& ctx);
Result apply_polar_array(const Json& modifier, const Produce& produce, const Variables& variables,
                         const ModifierContext& ctx);
Result apply_mirror(const Json& modifier, const Produce& produce, const Variables& variables, const ModifierContext& ctx);
Result apply_corners(const Json& modifier, const Produce& produce, const Variables& variables,
                     const ModifierContext& ctx);

// -- shared by the modifiers --

// A copy count: a non-negative integer (what: "array columns").
int copy_count(const Context& ctx, const Json& modifier, const char* key, double fallback, const char* what);

// The self.<point>.<axis> coordinates the modifier uses, measured on ``own``,
// added to ``variables``.
Variables with_self(const Json& modifier, const Result& own, const Variables& variables,
                    const std::vector<std::string>& extra_names = {});

// Whether the modifier uses self points (then it measures its input first).
bool uses_self(const Json& modifier, const std::vector<std::string>& extra_names = {});

// A point (node.point, or self.point from ``own``) as the modifier sees it.
mgeom::Point point_of(const std::string& reference, const Result& own, const Scope& scope, const std::string& what);

// Geometry moved, rotated or mirrored.
Layers placed(const Layers& layers, const mgeom::Transform& t);

}  // namespace mems
