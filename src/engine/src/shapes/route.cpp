// A wire from one port to another (mems_sketch.core.shapes.kinds.route; the
// centreline: mgeom::route). Its ports are where the start and end shapes say,
// so it follows them when they move. It is as wide as its ports and on their
// layer unless given; ports of different widths need a width (a taper waits
// for variable-width paths). Like a path, its own ports start and end are
// where it ends, pointing out of it (into the parts it joins).
#include <cmath>
#include <cstdio>
#include <numbers>
#include <string>
#include <tuple>
#include <vector>

#include "kinds.hpp"
#include "mgeom/route.hpp"

namespace mems {

namespace {

std::string number(double v) {
    char buffer[32];
    std::snprintf(buffer, sizeof buffer, "%g", v);
    return buffer;
}

// The way the wire goes at its start (a unit vector).
mgeom::Point start_direction(const mgeom::Wire& wire) {
    const auto& first = wire.segments().front();
    const mgeom::Point a = wire.start();
    if (first.arc) {  // the tangent: the radius turned a quarter
        const mgeom::Point r{a.x - first.centre.x, a.y - first.centre.y};
        const double l = std::hypot(r.x, r.y);
        return first.ccw ? mgeom::Point{-r.y / l, r.x / l} : mgeom::Point{r.y / l, -r.x / l};
    }
    const double l = std::hypot(first.end.x - a.x, first.end.y - a.y);
    return {(first.end.x - a.x) / l, (first.end.y - a.y) / l};
}

}  // namespace

Result render_route(const Json& node, const Context& ctx) {
    const auto reference = [&](const char* key) {
        if (!node.contains(key) || !node[key].is_string() || node[key].get<std::string>().empty())
            throw BuildError(std::string("a route needs a port to ") + (key == std::string("start") ? "start" : "end") +
                             " at, like 'pad.port.signal'");
        return node[key].get<std::string>();
    };
    const std::string start_name = reference("start"), end_name = reference("end");
    if (start_name == end_name) throw BuildError("a route's start and end are the same port '" + start_name + "'");
    const Port start = port_of(ctx, start_name, "start").second;
    const Port end = port_of(ctx, end_name, "end").second;

    double width = ctx.number(node, "width", 0.0);
    if (width < 0) throw BuildError("a route's width cannot be negative");
    if (width == 0) {
        if (start.width > 0 && end.width > 0 && std::abs(start.width - end.width) > 1e-9)
            throw BuildError("its ports are of different widths (" + number(start.width) + ", " + number(end.width) +
                             "): give the route a width");
        width = start.width > 0 ? start.width : end.width;
        if (!(width > 0)) throw BuildError("its ports have no width: give the route one");
    }
    std::string layer;
    if (node.contains("layer") && node["layer"].is_string()) {
        layer = ctx.layer(node);
    } else {
        if (start.layer != end.layer)
            throw BuildError("its ports are on different layers ('" + start.layer + "', '" + end.layer +
                             "'): give the route a layer");
        layer = start.layer;
    }

    std::vector<mgeom::Point> via;
    for (const auto& point : node.value("via", Json::array())) via.push_back({ctx.value(point.at(0)), ctx.value(point.at(1))});
    const std::string style = node.value("style", std::string("manhattan"));
    const std::string bend = node.value("bend", std::string("sharp"));
    const double radius = ctx.number(node, "radius", 0.0);
    if (radius < 0) throw BuildError("a route's bend radius must not be negative, not " + number_text(radius));
    const mgeom::Wire centreline = mgeom::route(
        {start.at, start.angle_deg}, {end.at, end.angle_deg}, via,
        style == "straight" ? mgeom::RouteStyle::straight
        : style == "l"      ? mgeom::RouteStyle::l
                            : mgeom::RouteStyle::manhattan,
        bend == "round" ? mgeom::Bend::round : bend == "chamfer" ? mgeom::Bend::chamfer : mgeom::Bend::sharp,
        radius, width);

    Result result{Layers{{layer, mgeom::Region::path(centreline, width)}}, {}};
    const mgeom::Point leaving = start_direction(centreline), arriving = centreline.end_direction();
    for (const auto& [name, at, out] : {std::tuple{"start", centreline.start(), mgeom::Point{-leaving.x, -leaving.y}},
                                        std::tuple{"end", centreline.end(), arriving}}) {
        const Port port{at, port_angle(std::atan2(out.y, out.x) * 180 / std::numbers::pi), width, layer};
        result.points.emplace(std::string("port.") + name, port.at);
        result.ports.emplace(name, port);
    }
    return result;
}

}  // namespace mems
