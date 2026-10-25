// A wire of constant width along a centreline (mems_sketch.core.shapes.kinds.path):
// flush ends, or extended by half the width (square), or round. Its ports start
// and end are where it ends, pointing out of it, as wide as it and on its layer
// (at the flat end: past a square end's extension, before a round end's cap).
// Its rotation turns it all about the centre of its points' box.
#include <cmath>
#include <numbers>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

#include "kinds.hpp"
#include "mgeom/wire.hpp"

namespace mems {

Result render_path(const Json& node, const Context& ctx) {
    const Json& points = node.at("points");
    if (points.size() < 2) throw BuildError("a path needs at least 2 points");
    std::vector<mgeom::Point> at;
    for (const auto& point : points) at.push_back({ctx.value(point.at(0)), ctx.value(point.at(1))});
    mgeom::Wire centreline(at.front());
    for (size_t k = 1; k < at.size(); ++k) centreline.line_to(at[k]);
    const double width = ctx.number(node, "width");
    if (width <= 0) throw BuildError("path width must be positive");
    const std::string ends = node.value("ends", std::string("flush"));
    const mgeom::PathEnds end = ends == "round" ? mgeom::PathEnds::round
                                : ends == "square" ? mgeom::PathEnds::square
                                                   : mgeom::PathEnds::flush;
    const auto region = mgeom::Region::path(centreline, width, end);
    if (region.empty()) return {};
    const std::string layer = ctx.layer(node);
    Result result{Layers{{layer, region}}, {}};
    const auto& segments = centreline.segments();
    const mgeom::Point first = segments.front().end, last = centreline.end();
    const mgeom::Point before = segments.size() > 1 ? segments[segments.size() - 2].end : centreline.start();
    for (const auto& [name, at, towards] :
         {std::tuple{"start", centreline.start(), first}, std::tuple{"end", last, before}}) {
        const double length = std::hypot(at.x - towards.x, at.y - towards.y);
        const mgeom::Point out{(at.x - towards.x) / length, (at.y - towards.y) / length};  // out of the path
        const double past = end == mgeom::PathEnds::square ? width / 2 : 0.0;
        const Port port{{at.x + out.x * past, at.y + out.y * past},
                        port_angle(std::atan2(out.y, out.x) * 180 / std::numbers::pi), width, layer};
        result.points.emplace(std::string("port.") + name, port.at);
        result.ports.emplace(name, port);
    }
    return turned(std::move(result), node, ctx, centre_of(at));
}

}  // namespace mems
