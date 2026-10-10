// Chosen corners of the node rounded or chamfered, each with its own radius
// (mems_sketch.core.shapes.modifiers.CornersModifier). A corner is found by
// its position (a point of the node or of another shape, or x, y) among the
// vertices of what the node makes; one that is not there is an error.
#include <cmath>
#include <vector>

#include "mgeom/types.hpp"
#include "modifiers.hpp"

namespace mems {

namespace {

constexpr double CORNER_MATCH_UM = 0.002;

bool is_corner_of(const mgeom::Region& region, const mgeom::Point& at) {
    for (const auto& corner : region.corners())
        if (std::abs(corner.at.x - at.x) <= CORNER_MATCH_UM && std::abs(corner.at.y - at.y) <= CORNER_MATCH_UM) return true;
    return false;
}

}  // namespace

Result apply_corners(const Json& modifier, const Produce& produce, const Variables& given, const ModifierContext& m) {
    const Result made = produce(given);
    const Json corners = modifier.value("corners", Json::array());
    if (corners.empty()) return made;
    std::vector<std::string> at_names;
    for (const auto& corner : corners)
        if (corner.contains("at") && corner["at"].is_string()) {
            at_names.push_back(corner["at"].get<std::string>() + ".x");
            at_names.push_back(corner["at"].get<std::string>() + ".y");
        }
    const Variables variables = with_self(modifier, made, given, at_names);
    const Context ctx{m.builder, m.component, variables, m.scope};

    std::vector<mgeom::CornerRounding> wanted;
    for (const auto& corner : corners) {
        mgeom::Point at;
        if (corner.contains("at") && corner["at"].is_string()) {
            const std::string where = corner["at"].get<std::string>();
            at = point_of(where, made, m.scope, "corner at '" + where + "'");
        } else {
            at = {ctx.number(corner, "x", 0.0), ctx.number(corner, "y", 0.0)};
        }
        const double radius = ctx.number(corner, "radius", 1.0);
        if (radius < 0) throw BuildError("corner radius must not be negative");
        const bool chamfer = corner.value("style", std::string("round")) == "chamfer";
        wanted.push_back({at, radius, chamfer ? mgeom::CornerStyle::chamfer : mgeom::CornerStyle::round});
    }
    // Each layer gets the corners that are its own; every corner must be someone's.
    std::vector<bool> found(wanted.size(), false);
    Layers result;
    for (const auto& [layer, region] : made.flat()) {  // a placed component's corners too
        std::vector<mgeom::CornerRounding> own;
        for (size_t k = 0; k < wanted.size(); ++k) {
            if (is_corner_of(region, wanted[k].at)) {
                own.push_back(wanted[k]);
                found[k] = true;
            }
        }
        result.emplace(layer, own.empty() ? region : region.rounded(own));
    }
    for (size_t k = 0; k < wanted.size(); ++k) {
        if (!found[k]) {
            char buffer[64];
            std::snprintf(buffer, sizeof buffer, "(%g, %g) is not a corner of the shape", wanted[k].at.x, wanted[k].at.y);
            throw BuildError(buffer);
        }
    }
    return {result, made.points};
}

}  // namespace mems
