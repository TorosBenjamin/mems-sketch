// Evaluating shape trees, as mems_sketch.core.shapes.render does: sibling
// nodes in the order their alignments and point coordinates need, each with
// its modifiers applied first to last around it and then aligned, everything
// on a layer merged; then a component's declared points.
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <functional>
#include <set>

#include "../model/shape_tree.hpp"
#include "../shapes/kinds.hpp"
#include "context.hpp"
#include "mgeom/types.hpp"

namespace mems {

namespace {

constexpr double DBU_UM = 0.001;  // alignment moves are snapped to the database grid, as in Python

const std::vector<std::string> BBOX_POINTS = {"center",   "left",      "right",       "top",         "bottom",
                                              "top_left", "top_right", "bottom_left", "bottom_right"};

bool is_bbox_point(const std::string& name) {
    return std::find(BBOX_POINTS.begin(), BBOX_POINTS.end(), name) != BBOX_POINTS.end();
}

std::string format(double v) {
    char buffer[32];
    std::snprintf(buffer, sizeof buffer, "%g", v);
    return buffer;
}

std::string label_of(const Json& node) {
    if (node.contains("name") && node["name"].is_string()) return node["name"].get<std::string>();
    return node.value("kind", std::string("shape"));
}

std::string name_of(const Json& node) {
    return node.contains("name") && node["name"].is_string() ? node["name"].get<std::string>() : std::string();
}

// node.point.x: a point coordinate (node, point, axis), or nothing.
struct PointName {
    std::string node, point;
    int axis;
};
std::optional<PointName> point_name(const std::string& name) {
    const size_t first = name.find('.');
    if (first == std::string::npos) return std::nullopt;
    const size_t second = name.find('.', first + 1);
    if (second == std::string::npos || name.find('.', second + 1) != std::string::npos) return std::nullopt;
    const std::string axis = name.substr(second + 1);
    if (axis != "x" && axis != "y") return std::nullopt;
    return PointName{name.substr(0, first), name.substr(first + 1, second - first - 1), axis == "x" ? 0 : 1};
}

// The point coordinates a string uses (when it is an expression).
std::vector<PointName> point_names(const std::string& text) {
    std::vector<PointName> found;
    if (std::count(text.begin(), text.end(), '.') < 2) return found;
    try {
        for (const auto& name : names_in(text))
            if (auto point = point_name(name)) found.push_back(*point);
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

// The names of the nodes whose points a node or its subtree uses.
void point_dependencies(const Json& node, std::set<std::string>& found) {
    if (node.value("enabled", true)) {
        if (node.contains("align") && node["align"].is_object())
            found.insert(node["align"].value("to", std::string()).substr(0, node["align"].value("to", std::string()).find('.')));
        std::vector<std::string> texts;
        for (const auto& [key, value] : node.items())
            if (!is_child_list(key, value)) own_strings(value, texts);
        for (const auto& text : texts)
            for (const auto& point : point_names(text)) found.insert(point.node);
    }
    for (const auto& [key, value] : node.items())
        if (is_child_list(key, value))
            for (const auto& child : value) point_dependencies(child, found);
}

// A copy count: a non-negative integer.
int count(const Context& ctx, const Json& modifier, const char* key, const char* what) {
    const double number = ctx.number(modifier, key, 1.0);
    if (number != std::trunc(number) || number < 0)
        throw BuildError(std::string(what) + " must be a non-negative integer, got " + format(number));
    return static_cast<int>(number);
}

using Produce = std::function<Result(const Variables&)>;

// The self.<point>.<axis> names a modifier uses.
std::vector<PointName> self_names(const Json& modifier) {
    std::vector<std::string> texts;
    own_strings(modifier, texts);
    std::vector<PointName> found;
    for (const auto& text : texts)
        for (const auto& point : point_names(text))
            if (point.node == "self") found.push_back(point);
    return found;
}

// ``variables`` plus the self points the modifier uses, measured on its input.
Variables with_self(const Json& modifier, const Produce& produce, const Variables& variables) {
    const auto names = self_names(modifier);
    if (names.empty()) return variables;
    const Result own = produce(variables);
    const Points self{"self", bbox_of(own.layers), own.points, {}};
    Variables result = variables;
    for (const auto& name : names) {
        const mgeom::Point p = self.point(name.point);
        result["self." + name.point + (name.axis == 0 ? ".x" : ".y")] = name.axis == 0 ? p.x : p.y;
    }
    return result;
}

// Copies on a grid; the copy's column and row are i and j.
Result apply_array(const Json& modifier, const Produce& produce, const Variables& given, Builder& builder,
                   const std::string& component, const Scope& scope) {
    const Variables variables = with_self(modifier, produce, given);
    const Context ctx{builder, component, variables, scope};
    const int columns = count(ctx, modifier, "columns", "array columns");
    const int rows = count(ctx, modifier, "rows", "array rows");
    const double dx = ctx.number(modifier, "dx", 0.0), dy = ctx.number(modifier, "dy", 0.0);
    LayerSet layers;
    std::optional<PointMap> declared;
    for (int j = 0; j < rows; ++j) {
        for (int i = 0; i < columns; ++i) {
            Variables copy = variables;
            copy["i"] = i;
            copy["j"] = j;
            const mgeom::Transform move = mgeom::Transform::translation(i * dx, j * dy);
            Result made = produce(copy);
            for (const auto& [layer, region] : made.layers) layers.add(layer, region.transformed(move));
            if (!declared) declared = made.points;  // the first copy sits at the node's own place
        }
    }
    return {layers.merged(), declared.value_or(PointMap{})};
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
    Points points;
};

NodeResult render_node(const Json& node, Builder& builder, const std::string& component, const Variables& variables,
                       const Scope& scope) {
    const std::string kind = node.value("kind", std::string());
    const RenderKind render = find_kind(kind);
    if (!render) throw NotSupported("'" + kind + "' shapes are not built by the engine yet");

    Produce produce = [&](const Variables& v) { return render(node, Context{builder, component, v, scope}); };
    for (const auto& modifier : node.value("modifiers", Json::array())) {
        if (!modifier.value("enabled", true)) continue;
        const std::string modifier_kind = modifier.value("kind", std::string());
        if (modifier_kind != "array")
            throw NotSupported("'" + modifier_kind + "' modifiers are not built by the engine yet");
        produce = [modifier, inner = produce, &builder, &component, &scope](const Variables& v) {
            return apply_array(modifier, inner, v, builder, component, scope);
        };
    }
    Variables first = variables;  // the first copy: i and j are 0
    first["i"] = 0.0;
    first["j"] = 0.0;
    Result made = produce(first);
    NodeResult result{std::move(made.layers), Points{label_of(node), {}, std::move(made.points), {}}};
    result.points.box = bbox_of(result.layers);
    if (node.contains("align") && !node["align"].is_null()) {
        const mgeom::Transform shift = alignment(node, result.points, first, scope, builder, component);
        for (auto& [layer, region] : result.layers) region = region.transformed(shift);
        for (auto& [name, point] : result.points.declared) point = shift.apply(point);
        result.points.box = bbox_of(result.layers);
    }
    return result;
}

}  // namespace

// -- points ------------------------------------------------------------------------

mgeom::Point Points::point(const std::string& point) const {
    mgeom::Point p;
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

Points Points::seen_through(const mgeom::Transform& t) const {
    Points result = *this;
    result.transform = t * transform;
    return result;
}

mgeom::Box bbox_of(const Layers& layers) {
    mgeom::Box box;
    for (const auto& [layer, region] : layers) {
        const mgeom::Box b = region.bbox();
        if (b.empty()) continue;
        box.x0 = std::min(box.x0, b.x0), box.y0 = std::min(box.y0, b.y0);
        box.x1 = std::max(box.x1, b.x1), box.y1 = std::max(box.y1, b.y1);
    }
    return box;
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
        const auto point = point_name(name);
        if (!point) continue;
        const auto found = scope.find(point->node);
        if (found == scope.end()) continue;  // an unknown variable, as in Python
        const mgeom::Point p = found->second.point(point->point);
        extra[name] = point->axis == 0 ? p.x : p.y;
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
    return render_lists(lists, builder, component, variables, replaced ? *replaced : scope).layers;
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
    struct Entry {
        size_t slot;
        const Json* node;
    };
    std::vector<Entry> entries;
    std::map<std::string, size_t> named;
    for (size_t slot = 0; slot < lists.size(); ++slot) {
        for (const auto& node : *lists[slot]) {
            if (!node.value("enabled", true)) continue;
            if (const std::string name = name_of(node); !name.empty()) named[name] = entries.size();
            entries.push_back({slot, &node});
        }
    }
    std::vector<std::optional<Layers>> results(entries.size());
    std::vector<bool> visiting(entries.size(), false);
    Rendered rendered;

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
        try {
            result = render_node(node, builder, component, variables, visible);
        } catch (const mgeom::GeometryError& error) {
            throw BuildError("'" + label_of(node) + "': " + error.what());
        }
        visiting[k] = false;
        results[k] = std::move(result.layers);
        if (const std::string name = name_of(node); !name.empty()) rendered.local.insert_or_assign(name, std::move(result.points));
    };
    for (size_t k = 0; k < entries.size(); ++k) visit(k);

    std::vector<LayerSet> sets(lists.size());
    for (size_t k = 0; k < entries.size(); ++k) sets[entries[k].slot].add(*results[k]);
    for (const auto& set : sets) rendered.layers.push_back(set.merged());
    return rendered;
}

// -- the builder ------------------------------------------------------------------

const Built& Builder::build(std::string_view component, const Values& params) {
    const std::string qualified = project_.qualify(component);
    const ComponentDef* definition = project_.definition(qualified);
    if (!definition)
        throw NotSupported("'" + qualified + "' is a built-in or imported component: Python builds those for now");
    const auto values = project_.variables(qualified, params);
    std::string key = project_.fingerprint(qualified);
    for (const auto& [name, value] : values) {
        char buffer[40];
        std::snprintf(buffer, sizeof buffer, "%.17g", value);
        key += "|" + name + "=" + buffer;
    }
    if (const auto found = cache_.find(key); found != cache_.end()) return found->second;
    const Variables variables(values.begin(), values.end());
    Rendered rendered = render_lists({&definition->shapes->json}, *this, qualified, variables, Scope{});

    // Its declared points, measured from its top-level shapes or its own box.
    Built built{std::move(rendered.layers.front()), {}};
    const Points whole{definition->name, bbox_of(built.layers), {}, {}};
    Variables first = variables;
    first["i"] = 0.0;
    first["j"] = 0.0;
    const Context ctx{*this, qualified, first, rendered.local};
    for (const auto& point : definition->shapes->points) {
        const std::string name = point.at("name").get<std::string>();
        mgeom::Point base{0.0, 0.0};
        if (point.contains("at") && point["at"].is_string()) {
            const std::string at = point["at"].get<std::string>();
            if (is_bbox_point(at)) {
                base = whole.point(at);
            } else {
                const std::string node = at.substr(0, at.find('.'));
                const auto found = rendered.local.find(node);
                if (found == rendered.local.end())
                    throw BuildError("point '" + name + "' is at '" + at + "', but there is no shape '" + node + "'");
                base = found->second.point(at.substr(at.find('.') + 1));
            }
        }
        built.points[name] = {base.x + ctx.number(point, "x", 0.0), base.y + ctx.number(point, "y", 0.0)};
    }
    return cache_.emplace(key, std::move(built)).first->second;
}

}  // namespace mems
