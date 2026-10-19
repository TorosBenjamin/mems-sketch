// The area that joins beam ends, made from their ports
// (mems_sketch.core.shapes.kinds.connection; the outline: mgeom::connection).
// Each end is a placed component's or a path's port, ``beam.port.end``. Its
// edge (through the port, as wide as the port, across its direction) must lie
// on the beam's end: the beam's material has to be behind it all the way
// across, on the port's layer, or the connection would hang off nothing (a
// port in empty space or facing the wrong way), or leave gaps (a port on a
// rounded corner, or wider than its beam).
#include <algorithm>
#include <cmath>
#include <numbers>
#include <set>
#include <vector>

#include "kinds.hpp"
#include "mgeom/connection.hpp"

namespace mems {

namespace {

constexpr double REACH_UM = 0.5;  // how far it reaches into each beam: a quarter of the width at most

// Whether the beam is behind an end, all the way across and ``depth`` deep.
void check_on_beam(const Points& beam, const Port& port, double width, double depth, const std::string& end) {
    const double a = port.angle_deg * std::numbers::pi / 180;
    const mgeom::Point d{std::cos(a), std::sin(a)}, half{-d.y * width / 2, d.x * width / 2};
    const mgeom::Point back{d.x * depth, d.y * depth};
    const mgeom::Point strip_corners[] = {
        {port.at.x + half.x, port.at.y + half.y},
        {port.at.x - half.x, port.at.y - half.y},
        {port.at.x - half.x - back.x, port.at.y - half.y - back.y},
        {port.at.x + half.x - back.x, port.at.y + half.y - back.y},
    };
    const mgeom::Region strip = mgeom::Region::polygon(strip_corners);
    mgeom::Region material;
    if (beam.outline) {
        const Layers flat = flatten(beam.outline->layers(), beam.outline->instances());
        if (const auto found = flat.find(port.layer); found != flat.end()) material = found->second;
    }
    if (!beam.transform.is_identity()) material = material.transformed(beam.transform);
    const double missing = (strip - material).area();
    if (missing <= 1e-6 * strip.area()) return;
    if ((strip & material).empty())
        throw BuildError("end '" + end + "' is not on its shape: nothing of " + beam.name + " on layer '" + port.layer +
                         "' behind the port (it is off the shape, or faces the wrong way)");
    throw BuildError("end '" + end + "': " + beam.name +
                     " is not square across the end there (a rounded corner, or narrower than the port's width)");
}

}  // namespace

std::pair<const Points*, Port> port_of(const Context& ctx, const std::string& reference, const std::string& role) {
    const std::string what = role + " '" + reference + "'";
    const size_t dot = reference.find('.');
    if (dot == std::string::npos || reference.compare(dot + 1, 5, "port.") != 0 || reference.size() <= dot + 6)
        throw BuildError(what + " is not a port, like 'beam.port.end'");
    const std::string shape = reference.substr(0, dot);
    const auto found = ctx.scope.find(shape);
    if (found == ctx.scope.end()) throw BuildError(what + ": there is no shape '" + shape + "'");
    try {
        return {&found->second, found->second.port(reference.substr(dot + 6))};
    } catch (const BuildError& error) {
        throw BuildError(what + ": " + error.what());
    }
}

Result render_connection(const Json& node, const Context& ctx) {
    const bool own_layer = node.contains("layer") && node["layer"].is_string();
    std::string layer = own_layer ? ctx.layer(node) : std::string();
    const double fallback_width = ctx.number(node, "width", 0.0);
    std::vector<mgeom::ConnectionEnd> ends;
    std::vector<std::pair<const Points*, Port>> beams;
    std::set<std::string> named;
    for (const auto& item : node.value("ends", Json::array())) {
        const std::string end = item.is_string() ? item.get<std::string>() : std::string();
        if (!named.insert(end).second) throw BuildError("end '" + end + "' is named twice");
        const auto [beam, port] = port_of(ctx, end, "end");
        const double width = port.width > 0 ? port.width : fallback_width;
        if (!(width > 0)) throw BuildError("end '" + end + "': its port has no width, so give the connection one");
        if (!own_layer) {
            if (layer.empty()) layer = port.layer;
            if (port.layer != layer)
                throw BuildError("its ends are on different layers ('" + layer + "', '" + port.layer +
                                 "'): give the connection a layer");
        }
        ends.push_back({port.at, port.angle_deg, width, end});
        beams.emplace_back(beam, port);
    }
    double reach = REACH_UM;
    for (const auto& end : ends) reach = std::min(reach, end.width / 4);
    for (size_t k = 0; k < ends.size(); ++k) check_on_beam(*beams[k].first, beams[k].second, ends[k].width, reach, ends[k].label);
    const mgeom::Region area = mgeom::connection(ends, ctx.number(node, "inner_radius", 0.0), ctx.number(node, "radius", 0.0), reach);
    if (layer.empty()) return {};
    return {Layers{{layer, area}}, {}};
}

}  // namespace mems
