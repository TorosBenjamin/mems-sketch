// A wire of constant width along a centreline (mems_sketch.core.shapes.kinds.path):
// flush ends, or extended by half the width (square), or round.
#include "kinds.hpp"
#include "mgeom/wire.hpp"

namespace mems {

Result render_path(const Json& node, const Context& ctx) {
    const Json& points = node.at("points");
    if (points.size() < 2) throw BuildError("a path needs at least 2 points");
    mgeom::Wire centreline({ctx.value(points[0].at(0)), ctx.value(points[0].at(1))});
    for (size_t k = 1; k < points.size(); ++k) centreline.line_to({ctx.value(points[k].at(0)), ctx.value(points[k].at(1))});
    const double width = ctx.number(node, "width");
    if (width <= 0) throw BuildError("path width must be positive");
    const std::string ends = node.value("ends", std::string("flush"));
    const mgeom::PathEnds end = ends == "round" ? mgeom::PathEnds::round
                                : ends == "square" ? mgeom::PathEnds::square
                                                   : mgeom::PathEnds::flush;
    const auto region = mgeom::Region::path(centreline, width, end);
    if (region.empty()) return {};
    return {Layers{{node.at("layer").get<std::string>(), region}}, {}};
}

}  // namespace mems
