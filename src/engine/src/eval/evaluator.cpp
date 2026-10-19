// Evaluating shape trees, as mems_sketch.core.shapes.render does: sibling
// nodes in the order their alignments and point coordinates need, each with
// its modifiers applied first to last around it and then aligned, everything
// on a layer merged; then a component's declared points.
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <functional>
#include <numbers>
#include <optional>
#include <set>

#include "../model/shape_tree.hpp"
#include "../modifiers/modifiers.hpp"
#include "../shapes/kinds.hpp"
#include "context.hpp"
#include "mgeom/interrupt.hpp"
#include "mgeom/types.hpp"

namespace mems {

namespace {

constexpr double DBU_UM = 0.001;  // alignment moves are snapped to the database grid, as in Python

const std::vector<std::string> BBOX_POINTS = {"center",   "left",      "right",       "top",         "bottom",
                                              "top_left", "top_right", "bottom_left", "bottom_right"};

bool is_bbox_point(const std::string& name) {
    return std::find(BBOX_POINTS.begin(), BBOX_POINTS.end(), name) != BBOX_POINTS.end();
}

std::string label_of(const Json& node) {
    if (node.contains("name") && node["name"].is_string()) return node["name"].get<std::string>();
    return node.value("kind", std::string("shape"));
}

std::string name_of(const Json& node) {
    return node.contains("name") && node["name"].is_string() ? node["name"].get<std::string>() : std::string();
}

// The measures a string uses (when it is an expression).
std::vector<MeasureName> measure_names(const std::string& text) {
    std::vector<MeasureName> found;
    if (text.find('.') == std::string::npos) return found;
    try {
        for (const auto& name : names_in(text))
            if (auto measure = measure_name(name)) found.push_back(*measure);
    } catch (const ExpressionError&) {
    }
    return found;
}

// Child lists of a node: arrays of nodes (modifiers are not nodes).
bool is_child_list(const std::string& key, const Json& value) {
    if (key == "modifiers" || !value.is_array() || value.empty()) return false;
    return std::all_of(value.begin(), value.end(), [](const Json& v) { return v.is_object() && v.contains("kind"); });
}

void own_strings(const Json& value, std::vector<std::string>& out) {
    if (value.is_string()) out.push_back(value.get<std::string>());
    else if (value.is_array())
        for (const auto& item : value) own_strings(item, out);
    else if (value.is_object())
        for (const auto& [key, item] : value.items()) own_strings(item, out);
}

// The names of the nodes whose points a node or its subtree uses (a switched-off
// node too: it is still built, for its points).
void point_dependencies(const Json& node, std::set<std::string>& found) {
    {
        if (node.contains("align") && node["align"].is_object())
            found.insert(node["align"].value("to", std::string()).substr(0, node["align"].value("to", std::string()).find('.')));
        std::vector<std::string> texts;
        for (const auto& [key, value] : node.items())
            if (!is_child_list(key, value)) own_strings(value, texts);
        for (const auto& text : texts)
            for (const auto& measure : measure_names(text)) found.insert(measure.node);
        // Nodes the modifiers name outside expressions: a mirror's about, a corner's at.
        for (const auto& modifier : node.value("modifiers", Json::array())) {
            if (modifier.contains("about") && modifier["about"].is_string()) {
                const std::string about = modifier["about"].get<std::string>();
                found.insert(about.substr(0, about.find('.')));
            }
            for (const auto& corner : modifier.value("corners", Json::array()))
                if (corner.contains("at") && corner["at"].is_string())
                    found.insert(corner["at"].get<std::string>().substr(0, corner["at"].get<std::string>().find('.')));
            for (const auto& name : modifier.value("keep_out", Json::array()))  // a perforation's
                if (name.is_string()) found.insert(name.get<std::string>());
        }
        if (node.value("kind", std::string()) == "connection")  // the ends it joins: beam.port.end
            for (const auto& end : node.value("ends", Json::array()))
                if (end.is_string()) found.insert(end.get<std::string>().substr(0, end.get<std::string>().find('.')));
        if (node.value("kind", std::string()) == "route")  // the ports it runs between
            for (const char* key : {"start", "end"})
                if (node.contains(key) && node[key].is_string())
                    found.insert(node[key].get<std::string>().substr(0, node[key].get<std::string>().find('.')));
    }
    for (const auto& [key, value] : node.items())
        if (is_child_list(key, value))
            for (const auto& child : value) point_dependencies(child, found);
}

// The move an alignment makes: the node's point onto the target, plus dx, dy.
mgeom::Transform alignment(const Json& node, const Points& own, const Variables& variables, const Scope& scope,
                           Builder& builder, const std::string& component) {
    const Json& align = node.at("align");
    const std::string to = align.at("to").get<std::string>();
    const std::string target_node = to.substr(0, to.find('.'));
    const std::string target_point = to.substr(to.find('.') + 1);
    const auto found = scope.find(target_node);
    if (found == scope.end())
        throw BuildError("'" + label_of(node) + "' is aligned to '" + to + "', but no shape named '" + target_node +
                         "' is visible from it");
    const mgeom::Point target = found->second.point(target_point);
    const mgeom::Point at = own.point(align.value("point", std::string("center")));
    const Context ctx{builder, component, variables, scope};
    const double dx = ctx.number(align, "dx", 0.0), dy = ctx.number(align, "dy", 0.0);
    // Snapped to the database grid, so that geometry and points agree (as in Python).
    const double move_x = std::nearbyint((target.x + dx - at.x) / DBU_UM) * DBU_UM;
    const double move_y = std::nearbyint((target.y + dy - at.y) / DBU_UM) * DBU_UM;
    return mgeom::Transform::translation(move_x, move_y);
}

struct NodeResult {
    Layers layers;
    Instances instances;
    Points points;
    mgeom::Transform shift, inner;  // its alignment's move; the frame of its children
};

// The placement a transform or a reference applies to what it holds.
mgeom::Transform placement_of(const Json& node, const Context& ctx) {
    const std::string kind = node.value("kind", std::string());
    if (kind != "transform" && kind != "group" && kind != "ref") return {};
    return mgeom::Transform{ctx.number(node, "x", 0.0), ctx.number(node, "y", 0.0), ctx.number(node, "rotation", 0.0),
                            node.value("mirror_x", false), kind == "ref" ? 1.0 : ctx.number(node, "scale", 1.0)};
}

NodeResult render_node(const Json& node, Builder& builder, const std::string& component, const Variables& variables,
                       const Scope& scope) {
    mgeom::check_interrupted();  // the build is no longer needed: stop before the next shape
    const std::string kind = node.value("kind", std::string());
    const RenderKind render = find_kind(kind);
    if (!render) throw NotSupported("'" + kind + "' shapes are not built by the engine yet");

    Produce produce = [&](const Variables& v) { return render(node, Context{builder, component, v, scope}); };
    const ModifierContext context{builder, component, scope};
    for (const auto& modifier : node.value("modifiers", Json::array())) {
        if (!modifier.value("enabled", true)) continue;
        const std::string modifier_kind = modifier.value("kind", std::string());
        const ApplyModifier apply = find_modifier(modifier_kind);
        if (!apply) throw NotSupported("'" + modifier_kind + "' modifiers are not built by the engine yet");
        produce = [modifier, apply, inner = produce, context](const Variables& v) {
            return apply(modifier, inner, v, context);
        };
    }
    Variables first = variables;  // the first copy: i and j are 0
    first["i"] = 0.0;
    first["j"] = 0.0;
    Result made = produce(first);
    NodeResult result{std::move(made.layers), std::move(made.instances),
                      Points{label_of(node), {}, std::move(made.points), {}, {}, std::move(made.ports)}, {}, {}};
    result.points.box = bbox_of(result.layers, result.instances);
    result.points.outline = std::make_shared<Outline>(result.layers, result.instances);
    if (node.contains("align") && !node["align"].is_null()) {
        result.shift = alignment(node, result.points, first, scope, builder, component);
        for (auto& [layer, region] : result.layers) region = region.transformed(result.shift);
        result.instances = placed(result.instances, result.shift);
        for (auto& [name, point] : result.points.declared) point = result.shift.apply(point);
        for (auto& [name, port] : result.points.ports) port = placed(port, result.shift);
        result.points.box = bbox_of(result.layers, result.instances);
        result.points.outline = std::make_shared<Outline>(result.layers, result.instances);
    }
    result.inner = result.shift * placement_of(node, Context{builder, component, first, scope});
    return result;
}

}  // namespace

// What the node cache keeps: the node as evaluated and, when it was evaluated
// while records were made, the records of the nodes inside it (by their path
// below it).
struct CachedNode {
    NodeResult result;
    bool has_records = false;
    std::vector<std::pair<NodePath, NodeRecord>> records;
};

namespace {

void strings_in(const Json& value, std::vector<std::string>& out) {
    if (value.is_string()) out.push_back(value.get<std::string>());
    else if (value.is_array() || value.is_object())
        for (const auto& item : value) strings_in(item, out);
}

void references_in(const Json& value, std::vector<std::string>& out) {
    if (value.is_object()) {
        if (value.value("kind", std::string()) == "ref" && value.contains("component") && value["component"].is_string())
            out.push_back(value["component"].get<std::string>());
        for (const auto& item : value) references_in(item, out);
    } else if (value.is_array()) {
        for (const auto& item : value) references_in(item, out);
    }
}

void append_number(std::string& key, double value) {
    char buffer[32];
    std::snprintf(buffer, sizeof buffer, "%.17g", value);
    key += buffer;
}

void append_points(std::string& key, const Points& p) {
    key += p.name;
    for (const double v : {p.box.x0, p.box.y0, p.box.x1, p.box.y1, p.transform.dx, p.transform.dy, p.transform.angle_deg,
                           p.transform.scale})
        key += ',', append_number(key, v);
    key += p.transform.mirror_x ? "m" : "-";
    for (const auto& [name, q] : p.declared) key += ';' + name + '=', append_number(key, q.x), key += ',', append_number(key, q.y);
    for (const auto& [name, port] : p.ports) {  // a port's direction, width and layer can be inherited
        key += '>' + name + '=';
        for (const double v : {port.at.x, port.at.y, port.angle_deg, port.width}) append_number(key, v), key += ',';
        key += port.layer;
    }
    char buffer[32];  // the geometry its minimal box is taken of: the same object for the same node result
    std::snprintf(buffer, sizeof buffer, "@%p", static_cast<const void*>(p.outline.get()));
    key += buffer;
}

// Everything evaluating ``node`` reads, as a key: the node itself, the values
// and the other nodes' points its expressions use, the components it places,
// the level and the layer stack. None when it cannot be told (then it is
// evaluated every time).
std::optional<std::string> node_key(const Json& node, Builder& builder, const std::string& component,
                                    const Variables& variables, const Scope& scope) {
    const Project& project = builder.project();
    std::string key = component;
    key += '\x1f';
    key += builder.level() ? *builder.level() : std::string("-");
    key += '\x1f';
    key += project.stack_key();
    key += '\x1f';
    key += node.dump();
    std::vector<std::string> texts;
    strings_in(node, texts);
    std::set<std::string> names;
    for (const auto& text : texts) {
        try {
            for (const auto& name : names_in(text)) names.insert(name);
        } catch (const ExpressionError&) {  // not an expression (a name, a layer)
        }
    }
    for (const auto& name : names) {
        if (const auto found = variables.find(name); found != variables.end()) {
            key += '\x1f' + name + '=';
            append_number(key, found->second);
        }
    }
    std::set<std::string> dependencies;
    point_dependencies(node, dependencies);
    for (const auto& name : dependencies) {
        key += '\x1f' + name + ':';
        if (const auto found = scope.find(name); found != scope.end()) append_points(key, found->second);
        else key += '!';
    }
    std::vector<std::string> references;
    references_in(node, references);
    for (const auto& reference : references) {
        try {
            key += '\x1f' + reference + '#' + project.fingerprint(project.qualify(reference, component));
        } catch (const std::exception&) {
            return std::nullopt;  // the evaluation reports it
        }
    }
    return key;
}

bool below(const NodePath& path, const NodePath& prefix) {
    return path.size() > prefix.size() && std::equal(prefix.begin(), prefix.end(), path.begin());
}

// render_node, from the node cache when nothing it reads has changed. While
// records are made, the records of the nodes inside it are kept with it and
// made again from there.
NodeResult cached_render(const Json& node, Builder& builder, const std::string& component, const Variables& variables,
                         const Scope& scope) {
    const auto key = node_key(node, builder, component, variables, scope);
    if (!key) return render_node(node, builder, component, variables, scope);
    Records* records = builder.recording();
    const NodePath& path = builder.path();
    const auto hit = builder.cache().find_node(*key);
    if (hit && (!records || hit->has_records)) {
        mgeom::check_interrupted();
        if (records)
            for (const auto& [relative, record] : hit->records) {
                NodePath full = path;
                full.insert(full.end(), relative.begin(), relative.end());
                records->emplace(std::move(full), record);
            }
        return hit->result;
    }
    auto made = std::make_shared<CachedNode>();
    made->result = render_node(node, builder, component, variables, scope);
    if (records) {
        made->has_records = true;
        for (auto it = records->lower_bound(path); it != records->end() && below(it->first, path); ++it)
            made->records.emplace_back(NodePath(it->first.begin() + path.size(), it->first.end()), it->second);
        // Built before without records: keep that result (other nodes' keys name its geometry).
        if (hit) made->result = hit->result;
    }
    builder.cache().put_node(*key, made);
    return made->result;
}

}  // namespace

// -- points ------------------------------------------------------------------------

mgeom::Point Points::point(const std::string& point) const {
    mgeom::Point p;
    if (point.rfind("min.", 0) == 0 && is_bbox_point(point.substr(4))) {
        const mgeom::MinimalBox m = minimal();  // already in this frame
        const mgeom::Box& b = m.box;
        const std::string at = point.substr(4);
        const double cu = (b.x0 + b.x1) / 2, cv = (b.y0 + b.y1) / 2;
        double u = cu, v = cv;
        if (at.find("left") != std::string::npos) u = b.x0;
        if (at.find("right") != std::string::npos) u = b.x1;
        if (at.find("bottom") != std::string::npos) v = b.y0;
        if (at.find("top") != std::string::npos) v = b.y1;
        return m.to_xy({u, v});
    }
    if (const auto found = declared.find(point); found != declared.end()) {
        p = found->second;
    } else if (is_bbox_point(point)) {
        double x0, y0, x1, y1;
        if (box.empty() && !declared.empty()) {  // nothing drawn (a guide): the box of its own points
            x0 = y0 = INFINITY;
            x1 = y1 = -INFINITY;
            for (const auto& [n, q] : declared) {
                x0 = std::min(x0, q.x), y0 = std::min(y0, q.y), x1 = std::max(x1, q.x), y1 = std::max(y1, q.y);
            }
        } else if (box.empty()) {
            throw BuildError("shape '" + name + "' has no geometry to align to");
        } else {
            x0 = box.x0, y0 = box.y0, x1 = box.x1, y1 = box.y1;
        }
        const double cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
        if (point == "center") p = {cx, cy};
        else if (point == "left") p = {x0, cy};
        else if (point == "right") p = {x1, cy};
        else if (point == "bottom") p = {cx, y0};
        else if (point == "top") p = {cx, y1};
        else {
            const std::string vertical = point.substr(0, point.find('_')), horizontal = point.substr(point.find('_') + 1);
            p = {horizontal == "left" ? x0 : x1, vertical == "bottom" ? y0 : y1};
        }
    } else {
        throw BuildError("shape '" + name + "' has no point '" + point + "'");
    }
    return transform.is_identity() ? p : transform.apply(p);
}

double Points::measure(const std::string& path) const {
    const auto distance = [](mgeom::Point a, mgeom::Point b) { return std::hypot(b.x - a.x, b.y - a.y); };
    if (path == "width") return distance(point("left"), point("right"));
    if (path == "height") return distance(point("bottom"), point("top"));
    const size_t dot = path.rfind('.');
    if (dot != std::string::npos && (path.substr(dot + 1) == "x" || path.substr(dot + 1) == "y")) {
        const mgeom::Point p = point(path.substr(0, dot));
        return path.substr(dot + 1) == "x" ? p.x : p.y;
    }
    if (path.rfind("port.", 0) == 0 && dot != std::string::npos) {
        const Port p = port(path.substr(5, dot - 5));
        if (path.substr(dot + 1) == "angle") return p.angle_deg;
        if (path.substr(dot + 1) == "width") return p.width;
    }
    if (path == "min.angle") return minimal().angle_deg;
    if (path == "min.width") return minimal().box.width();
    if (path == "min.height") return minimal().box.height();
    throw BuildError("shape '" + name + "' has no measure '" + path + "'");
}

constexpr double OUTLINE_CHORD_UM = 0.001;  // curves split within the database grid

mgeom::MinimalBox Points::minimal() const {
    std::vector<mgeom::Point> points;
    if (outline && !outline->hull().empty()) {
        points = outline->hull();
    } else {
        for (const auto& [n, q] : declared) points.push_back(q);  // nothing drawn (a guide)
    }
    if (points.empty()) throw BuildError("shape '" + name + "' has no geometry to measure");
    if (!transform.is_identity())
        for (auto& p : points) p = transform.apply(p);
    return mgeom::minimal_box(points, OUTLINE_CHORD_UM * std::abs(transform.scale));
}

const std::vector<mgeom::Point>& Outline::hull() const {
    std::call_once(once_, [this] {
        std::vector<mgeom::Point> points;
        const auto add = [&](const mgeom::Region& region) {
            const auto more = mgeom::convex_hull(mgeom::outline_points(region, OUTLINE_CHORD_UM));
            points.insert(points.end(), more.begin(), more.end());
        };
        for (const auto& [layer, region] : layers_) add(region);
        for (const auto& instance : instances_)
            for (const auto& [layer, region] : instance.built->flat()) add(region.transformed(instance.transform));
        hull_ = mgeom::convex_hull(points);
    });
    return hull_;
}

std::optional<MeasureName> measure_name(const std::string& name) {
    const size_t first = name.find('.');
    if (first == std::string::npos || first == 0) return std::nullopt;
    std::string path = name.substr(first + 1);
    // A point's name: one word, or a placed component's port (port.drive).
    const auto point_name = [](const std::string& text) {
        const std::string word = text.rfind("port.", 0) == 0 ? text.substr(5) : text;
        return !word.empty() && word.find('.') == std::string::npos;
    };
    const auto ending = [](const std::string& text, std::initializer_list<const char*> ends) {
        const size_t dot = text.rfind('.');
        if (dot == std::string::npos || dot == 0) return std::string();
        for (const char* end : ends)
            if (text.compare(dot + 1, std::string::npos, end) == 0) return text.substr(0, dot);
        return std::string();
    };
    const auto coordinate = [&](const std::string& text) {  // point.x, port.drive.y
        const std::string point = ending(text, {"x", "y"});
        return !point.empty() && point_name(point);
    };
    const auto port_measure = [&](const std::string& text) {  // port.drive.angle, port.drive.width
        const std::string point = ending(text, {"angle", "width"});
        return point.rfind("port.", 0) == 0 && point_name(point);
    };
    const bool min = path.rfind("min.", 0) == 0;
    const std::string rest = min ? path.substr(4) : std::string();
    if (path == "width" || path == "height" || coordinate(path) || port_measure(path) ||
        (min && (rest == "width" || rest == "height" || rest == "angle" || coordinate(rest))))
        return MeasureName{name.substr(0, first), std::move(path)};
    return std::nullopt;
}

Port Points::port(const std::string& name) const {
    const auto found = ports.find(name);
    if (found == ports.end()) throw BuildError("shape '" + this->name + "' has no port '" + name + "'");
    return placed(found->second, transform);
}

Points Points::seen_through(const mgeom::Transform& t) const {
    Points result = *this;
    result.transform = t * transform;
    return result;
}

namespace {

void include(mgeom::Box& box, const mgeom::Box& b) {
    if (b.empty()) return;
    box.x0 = std::min(box.x0, b.x0), box.y0 = std::min(box.y0, b.y0);
    box.x1 = std::max(box.x1, b.x1), box.y1 = std::max(box.y1, b.y1);
}

// Whether a transform keeps boxes boxes: quarter turns (and mirrors, scales).
bool keeps_boxes(const mgeom::Transform& t) {
    const double turns = t.angle_deg / 90.0;
    return std::abs(turns - std::nearbyint(turns)) < 1e-12;
}

}  // namespace

mgeom::Box bbox_of(const Layers& layers) {
    mgeom::Box box;
    for (const auto& [layer, region] : layers) include(box, region.bbox());
    return box;
}

mgeom::Box box_of(const Instance& instance) {
    const mgeom::Box& b = instance.built->box;
    if (b.empty()) return b;
    if (!keeps_boxes(instance.transform)) {  // turned by another angle: the box of the turned geometry
        mgeom::Box box;
        for (const auto& [layer, region] : instance.built->flat()) include(box, region.transformed(instance.transform).bbox());
        return box;
    }
    mgeom::Box box;
    for (const mgeom::Point& corner : {mgeom::Point{b.x0, b.y0}, mgeom::Point{b.x1, b.y0}, mgeom::Point{b.x1, b.y1},
                                       mgeom::Point{b.x0, b.y1}}) {
        const mgeom::Point p = instance.transform.apply(corner);
        include(box, mgeom::Box{p.x, p.y, p.x, p.y});
    }
    return box;
}

mgeom::Box bbox_of(const Layers& layers, const Instances& instances) {
    mgeom::Box box = bbox_of(layers);
    for (const auto& instance : instances) include(box, box_of(instance));
    return box;
}

double port_angle(double degrees) {
    double a = std::fmod(degrees, 360.0);
    if (a < 0) a += 360.0;
    a = std::round(a * 1e9) / 1e9;  // turning by 90° leaves no 89.99999999
    return a >= 360.0 ? 0.0 : a + 0.0;  // and no -0
}

Port placed(const Port& port, const mgeom::Transform& t) {
    const double a = port.angle_deg * std::numbers::pi / 180;
    const mgeom::Point at = t.apply(port.at), ahead = t.apply({port.at.x + std::cos(a), port.at.y + std::sin(a)});
    const double angle = std::atan2(ahead.y - at.y, ahead.x - at.x) * 180 / std::numbers::pi;
    return {at, port_angle(angle), port.width * t.scale, port.layer};
}

Instances placed(const Instances& instances, const mgeom::Transform& t) {
    Instances result;
    result.reserve(instances.size());
    for (const auto& instance : instances) result.push_back({instance.built, t * instance.transform});
    return result;
}

Layers flatten(const Layers& own, const Instances& instances) {
    LayerSet set;
    set.add(own);
    for (const auto& instance : instances)
        for (const auto& [layer, region] : instance.built->flat()) set.add(layer, region.transformed(instance.transform));
    return set.merged();
}

const Layers& Built::flat() const {
    std::call_once(flat_once_, [this] { flat_ = instances.empty() ? layers : flatten(layers, instances); });
    return flat_;
}

// -- Context and LayerSet ---------------------------------------------------------

double Context::value(const Json& value) const {
    if (value.is_number()) return value.get<double>();
    if (!value.is_string()) throw BuildError("a value is neither a number nor an expression: " + value.dump());
    const Expression expression(value.get<std::string>());
    // Point coordinates of the shapes it can see (beam.right.x), when it uses any.
    Variables extra;
    for (const auto& name : expression.names()) {
        if (variables.count(name)) continue;
        const auto measure = measure_name(name);
        if (!measure) continue;
        const auto found = scope.find(measure->node);
        if (found == scope.end()) continue;  // an unknown variable, as in Python
        extra[name] = found->second.measure(measure->path);
    }
    if (extra.empty()) return expression.evaluate(variables);
    Variables all = variables;
    all.insert(extra.begin(), extra.end());
    return expression.evaluate(all);
}

double Context::number(const Json& node, const char* key, double fallback) const {
    const auto found = node.find(key);
    if (found == node.end() || found->is_null()) return fallback;
    return value(*found);
}

double Context::number(const Json& node, const char* key) const {
    const auto found = node.find(key);
    if (found == node.end() || found->is_null()) throw BuildError(std::string("missing '") + key + "'");
    return value(*found);
}

std::vector<Layers> Context::children(const std::vector<const Json*>& lists, const Scope* replaced) const {
    Rendered rendered = render_lists(lists, builder, component, variables, replaced ? *replaced : scope);
    for (size_t k = 0; k < rendered.layers.size(); ++k)
        if (!rendered.instances[k].empty()) rendered.layers[k] = flatten(rendered.layers[k], rendered.instances[k]);
    return std::move(rendered.layers);
}

Result Context::parts(const Json& list, const Scope* replaced) const {
    Rendered rendered = render_lists({&list}, builder, component, variables, replaced ? *replaced : scope);
    return {std::move(rendered.layers.front()), {}, std::move(rendered.instances.front())};
}

void LayerSet::add(const Layers& layers) {
    for (const auto& [layer, region] : layers) add(layer, region);
}

void LayerSet::add(const std::string& layer, const mgeom::Region& region) {
    if (!region.empty()) parts_[layer].push_back(region);
}

Layers LayerSet::merged() const {
    Layers result;
    for (const auto& [layer, regions] : parts_) {
        mgeom::Region merged = regions.size() == 1 ? regions.front() : mgeom::Region::unite(regions);
        if (!merged.empty()) result.emplace(layer, std::move(merged));
    }
    return result;
}

Rendered render_lists(const std::vector<const Json*>& lists, Builder& builder, const std::string& component,
                      const Variables& variables, const Scope& scope) {
    // A switched-off node (enabled: false) is hidden, not removed: it is built, so
    // that shapes aligned to it or using its points stay where they are, but its
    // geometry is left out. If it cannot be built, it has no points either.
    struct Entry {
        size_t slot, index;  // its list, and its index there
        const Json* node;
        bool enabled;
    };
    std::vector<Entry> entries;
    std::map<std::string, size_t> named;
    for (size_t slot = 0; slot < lists.size(); ++slot) {
        size_t index = 0;
        for (const auto& node : *lists[slot]) {
            const size_t at = index++;
            if (const std::string name = name_of(node); !name.empty()) named[name] = entries.size();
            entries.push_back({slot, at, &node, node.value("enabled", true)});
        }
    }
    std::vector<std::optional<Layers>> results(entries.size());
    std::vector<Instances> placements(entries.size());
    std::vector<bool> visiting(entries.size(), false);
    Rendered rendered;

    // A hidden node that cannot be built: nothing, and no points.
    auto finish_hidden = [&](size_t k) {
        visiting[k] = false;
        results[k] = Layers{};
    };
    std::function<void(size_t)> visit = [&](size_t k) {
        if (results[k]) return;
        const Json& node = *entries[k].node;
        if (visiting[k]) throw BuildError("circular alignment involving '" + label_of(node) + "'");
        visiting[k] = true;
        std::set<std::string> dependencies;
        point_dependencies(node, dependencies);
        for (const auto& dependency : dependencies)
            if (const auto found = named.find(dependency); found != named.end()) visit(found->second);
        Scope visible = scope;
        for (const auto& [name, points] : rendered.local) visible.insert_or_assign(name, points);
        NodeResult result;
        NodePath& path = builder.path();
        path.emplace_back(int(entries[k].slot), int(entries[k].index));
        struct Pop {
            NodePath& path;
            ~Pop() { path.pop_back(); }
        } pop{path};
        try {
            result = cached_render(node, builder, component, variables, visible);
        } catch (const mgeom::Interrupted&) {
            throw;  // not the shape's fault, hidden or not
        } catch (const mgeom::GeometryError& error) {
            if (!entries[k].enabled) return finish_hidden(k);
            throw BuildError("'" + label_of(node) + "': " + error.what());
        } catch (const std::exception&) {  // a build or expression error
            if (!entries[k].enabled) return finish_hidden(k);
            throw;
        }
        if (!entries[k].enabled) {  // hidden: its points, not its geometry
            result.layers.clear();
            result.instances.clear();
        }
        if (Records* records = builder.recording(); records && !records->count(path))
            records->emplace(path, NodeRecord{result.layers, result.instances, result.points.name,
                                              result.points.declared, result.inner, result.shift,
                                              result.points.ports});
        visiting[k] = false;
        results[k] = std::move(result.layers);
        placements[k] = std::move(result.instances);
        if (const std::string name = name_of(node); !name.empty()) rendered.local.insert_or_assign(name, std::move(result.points));
    };
    for (size_t k = 0; k < entries.size(); ++k) visit(k);

    std::vector<LayerSet> sets(lists.size());
    rendered.instances.resize(lists.size());
    for (size_t k = 0; k < entries.size(); ++k) {
        sets[entries[k].slot].add(*results[k]);
        auto& into = rendered.instances[entries[k].slot];
        into.insert(into.end(), placements[k].begin(), placements[k].end());
    }
    for (const auto& set : sets) rendered.layers.push_back(set.merged());
    return rendered;
}

// -- the builder ------------------------------------------------------------------

std::shared_ptr<const Built> Builder::build(std::string_view component, const Values& params) {
    const std::string qualified = project_.qualify(component);
    const ComponentDef* definition = project_.definition(qualified);
    return build_on(qualified, params, project_.top_level(definition ? definition->level : std::nullopt));
}

namespace {

mgeom::Region region_of(const ImportedPolygon& polygon) {
    auto points = [](const Ring& ring) {
        std::vector<mgeom::Point> result;
        for (const auto& [x, y] : ring) result.push_back({x, y});
        return result;
    };
    mgeom::Region region = mgeom::Region::polygon(points(polygon.hull));
    for (const auto& hole : polygon.holes) region = region - mgeom::Region::polygon(points(hole));
    return region;
}

}  // namespace

std::shared_ptr<const Built> Builder::build_on(const std::string& qualified, const Values& params,
                                              const OptionalLevel& level) {
    const ComponentDef* definition = project_.definition(qualified);
    if (!definition) {
        const ImportedGeometry* geometry = project_.imported(qualified);
        if (!geometry) throw UnknownComponent("unknown component '" + qualified + "'");
        if (!params.empty()) throw BuildError("'" + qualified + "' is an imported cell: it has no parameters");
        if (const auto found = imported_.find(qualified); found != imported_.end()) return found->second;
        LayerSet layers;
        for (const auto& [layer, polygons] : *geometry)
            for (const auto& polygon : polygons) layers.add(layer, region_of(polygon));
        auto built = std::make_shared<Built>();
        built->key = "import:" + project_.fingerprint(qualified);
        built->layers = layers.merged();
        built->box = bbox_of(built->layers);
        return imported_.emplace(qualified, std::move(built)).first->second;
    }
    // What it places is never recorded: records are of one component's own nodes.
    struct Nested {
        Builder& builder;
        Records* recording;
        NodePath path;
        ~Nested() { builder.recording_ = recording, builder.path_ = std::move(path); }
    } nested{*this, recording_, std::move(path_)};
    recording_ = nullptr;
    path_.clear();
    const auto values = project_.variables(qualified, params);
    std::string key = project_.fingerprint(qualified) + "@" + (level ? *level : std::string("-")) + "@" +
                      project_.stack_key();
    for (const auto& [name, value] : values) {
        char buffer[40];
        std::snprintf(buffer, sizeof buffer, "%.17g", value);
        key += "|" + name + "=" + buffer;
    }
    if (auto found = cache_->find(key)) return found;
    struct Restore {  // the level of the component that placed this one, after it
        OptionalLevel& level;
        OptionalLevel saved;
        ~Restore() { level = std::move(saved); }
    } restore{level_, level_};
    level_ = level;
    const Variables variables(values.begin(), values.end());
    Rendered rendered = render_lists({&definition->shapes->json}, *this, qualified, variables, Scope{});

    // Its declared points, measured from its top-level shapes or its own box.
    auto made = std::make_shared<Built>();
    Built& built = *made;
    built.key = key;
    built.layers = std::move(rendered.layers.front());
    built.instances = std::move(rendered.instances.front());
    built.box = bbox_of(built.layers, built.instances);
    const Points whole{definition->name, built.box, {}, {}, std::make_shared<Outline>(built.layers, built.instances)};
    Variables first = variables;
    first["i"] = 0.0;
    first["j"] = 0.0;
    const Context ctx{*this, qualified, first, rendered.local};
    // Where a point or port is measured from: its ``at``, a point of a top-level
    // shape (a placed component's port ``pad.port.signal`` among them) or of its
    // own box, else the origin.
    auto base_of = [&](const Json& item, const char* what) -> mgeom::Point {
        if (!item.contains("at") || !item["at"].is_string()) return {0.0, 0.0};
        const std::string at = item["at"].get<std::string>();
        if (is_bbox_point(at) || (at.rfind("min.", 0) == 0 && is_bbox_point(at.substr(4)))) return whole.point(at);
        const std::string node = at.substr(0, at.find('.'));
        const auto found = rendered.local.find(node);
        if (found == rendered.local.end())
            throw BuildError(std::string(what) + " '" + item.at("name").get<std::string>() + "' is at '" + at +
                             "', but there is no shape '" + node + "'");
        return found->second.point(at.substr(at.find('.') + 1));
    };
    for (const auto& point : definition->shapes->points) {
        const mgeom::Point base = base_of(point, "point");
        built.points[point.at("name").get<std::string>()] = {base.x + ctx.number(point, "x", 0.0),
                                                             base.y + ctx.number(point, "y", 0.0)};
    }
    // Its ports, measured like its points. One at a placed component's port
    // takes that port's direction, width and layer unless it gives its own.
    for (const auto& port : definition->shapes->ports) {
        const std::string name = port.at("name").get<std::string>();
        const mgeom::Point base = base_of(port, "port");
        std::optional<Port> from;
        if (port.contains("at") && port["at"].is_string()) {
            const std::string at = port["at"].get<std::string>();
            const size_t dot = at.find('.');
            if (dot != std::string::npos && at.compare(dot + 1, 5, "port.") == 0)
                from = rendered.local.at(at.substr(0, dot)).port(at.substr(dot + 6));
        }
        auto given = [&](const char* key) { return port.contains(key) && !port[key].is_null(); };
        Port made_port;
        made_port.at = {base.x + ctx.number(port, "x", 0.0), base.y + ctx.number(port, "y", 0.0)};
        if (given("direction")) {
            const Json& direction = port["direction"];
            const std::string word = direction.is_string() ? direction.get<std::string>() : std::string();
            made_port.angle_deg = word == "right" ? 0.0
                                  : word == "up"  ? 90.0
                                  : word == "left" ? 180.0
                                  : word == "down" ? 270.0
                                                   : ctx.value(direction);
        } else if (from) {
            made_port.angle_deg = from->angle_deg;
        }
        made_port.angle_deg = port_angle(made_port.angle_deg);
        made_port.width = given("width") ? ctx.value(port["width"]) : from ? from->width : 0.0;
        if (made_port.width < 0) throw BuildError("port '" + name + "': its width cannot be negative");
        made_port.layer = given("layer") ? layer(port["layer"].get<std::string>()) : from ? from->layer : layer("level");
        built.ports[name] = made_port;
    }
    return cache_->put(std::move(made));
}

std::shared_ptr<const Built> BuildCache::find(const std::string& key) const {
    const std::lock_guard lock(mutex_);
    const auto found = built_.find(key);
    return found == built_.end() ? nullptr : found->second;
}

std::shared_ptr<const Built> BuildCache::put(std::shared_ptr<const Built> built) {
    const std::lock_guard lock(mutex_);
    // Past the limit, start again: what is in use is built again as it is asked for
    // (what is still placed somewhere stays alive with its instances).
    if (built_.size() >= max_entries_) built_.clear();
    return built_.try_emplace(built->key, std::move(built)).first->second;
}

std::shared_ptr<const CachedNode> BuildCache::find_node(const std::string& key) const {
    const std::lock_guard lock(mutex_);
    const auto found = nodes_.find(key);
    return found == nodes_.end() ? nullptr : found->second;
}

void BuildCache::put_node(const std::string& key, std::shared_ptr<const CachedNode> node) {
    const std::lock_guard lock(mutex_);
    if (nodes_.size() >= max_nodes_) nodes_.clear();  // as for components: start again
    nodes_.insert_or_assign(key, std::move(node));
}

size_t BuildCache::nodes() const {
    const std::lock_guard lock(mutex_);
    return nodes_.size();
}

void BuildCache::clear() {
    const std::lock_guard lock(mutex_);
    built_.clear();
    nodes_.clear();
}

size_t BuildCache::size() const {
    const std::lock_guard lock(mutex_);
    return built_.size();
}

Records Builder::records(std::string_view component, const Values& params, std::string* error) {
    const std::string qualified = project_.qualify(component);
    const ComponentDef* definition = project_.definition(qualified);
    Records records;
    if (!definition || project_.is_imported(qualified)) return records;  // an imported cell has no nodes
    struct Restore {
        Builder& builder;
        OptionalLevel level;
        ~Restore() { builder.recording_ = nullptr, builder.path_.clear(), builder.level_ = std::move(level); }
    } restore{*this, level_};
    try {
        const auto values = project_.variables(qualified, params);
        level_ = project_.top_level(definition->level);
        recording_ = &records;
        path_.clear();
        const Variables variables(values.begin(), values.end());
        render_lists({&definition->shapes->json}, *this, qualified, variables, Scope{});
    } catch (const mgeom::Interrupted&) {
        throw;
    } catch (const std::exception& e) {
        if (!error) throw;
        *error = e.what();
    }
    return records;
}

}  // namespace mems
