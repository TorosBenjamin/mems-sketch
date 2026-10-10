// Evaluating shape trees, as mems_sketch.core.shapes.render does: each enabled
// node in turn, its modifiers applied first to last around it, everything on
// a layer merged.
#include <algorithm>
#include <cmath>
#include <functional>
#include <cstdio>

#include "../shapes/kinds.hpp"
#include "../model/shape_tree.hpp"
#include "context.hpp"
#include "mgeom/transform.hpp"
#include "mgeom/types.hpp"

namespace mems {

namespace {

std::string format(double v) {
    char buffer[32];
    std::snprintf(buffer, sizeof buffer, "%g", v);
    return buffer;
}

std::string label_of(const Json& node) {
    if (node.contains("name") && node["name"].is_string()) return node["name"].get<std::string>();
    return node.value("kind", std::string("shape"));
}

// The names an expression uses that the engine cannot give yet: points of
// other nodes (beam.right.x) and of the node itself (self.top.y).
void check_supported(const Expression& expression, const Variables& variables) {
    for (const auto& name : expression.names()) {
        if (variables.count(name)) continue;
        const bool point = std::count(name.begin(), name.end(), '.') >= 2 &&
                           (name.ends_with(".x") || name.ends_with(".y"));
        if (point || name.rfind("self.", 0) == 0)
            throw NotSupported("point coordinates ('" + name + "') are not built by the engine yet");
    }
}

// A copy count: a non-negative integer.
int count(const Context& ctx, const Json& modifier, const char* key, const char* what) {
    const double number = ctx.number(modifier, key, 1.0);
    if (number != std::trunc(number) || number < 0)
        throw BuildError(std::string(what) + " must be a non-negative integer, got " + format(number));
    return static_cast<int>(number);
}

using Produce = std::function<Layers(const Variables&)>;

// Copies on a grid; the copy's column and row are i and j.
Layers apply_array(const Json& modifier, const Produce& produce, const Variables& variables, Builder& builder,
                   const std::string& component) {
    const Context ctx{builder, component, variables};
    const int columns = count(ctx, modifier, "columns", "array columns");
    const int rows = count(ctx, modifier, "rows", "array rows");
    const double dx = ctx.number(modifier, "dx", 0.0), dy = ctx.number(modifier, "dy", 0.0);
    LayerSet result;
    for (int j = 0; j < rows; ++j) {
        for (int i = 0; i < columns; ++i) {
            Variables copy = variables;
            copy["i"] = i;
            copy["j"] = j;
            const mgeom::Transform move = mgeom::Transform::translation(i * dx, j * dy);
            for (const auto& [layer, region] : produce(copy)) result.add(layer, region.transformed(move));
        }
    }
    return result.merged();
}

Layers render_node(const Json& node, Builder& builder, const std::string& component, const Variables& variables) {
    const std::string kind = node.value("kind", std::string());
    const RenderKind render = find_kind(kind);
    if (!render) throw NotSupported("'" + kind + "' shapes are not built by the engine yet");
    if (node.contains("align") && !node["align"].is_null())
        throw NotSupported("alignment ('" + label_of(node) + "') is not built by the engine yet");

    Produce produce = [&](const Variables& v) { return render(node, Context{builder, component, v}); };
    for (const auto& modifier : node.value("modifiers", Json::array())) {
        if (!modifier.value("enabled", true)) continue;
        const std::string modifier_kind = modifier.value("kind", std::string());
        if (modifier_kind != "array")
            throw NotSupported("'" + modifier_kind + "' modifiers are not built by the engine yet");
        produce = [modifier, inner = produce, &builder, &component](const Variables& v) {
            return apply_array(modifier, inner, v, builder, component);
        };
    }
    Variables first = variables;  // the first copy: i and j are 0
    first["i"] = 0.0;
    first["j"] = 0.0;
    return produce(first);
}

}  // namespace

// -- Context and LayerSet ---------------------------------------------------------

double Context::value(const Json& value) const {
    if (value.is_number()) return value.get<double>();
    if (!value.is_string()) throw BuildError("a value is neither a number nor an expression: " + value.dump());
    const Expression expression(value.get<std::string>());
    check_supported(expression, variables);
    return expression.evaluate(variables);
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

Layers Context::children(const Json& list) const { return render_list(list, builder, component, variables); }

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

Layers render_list(const Json& list, Builder& builder, const std::string& component, const Variables& variables) {
    LayerSet result;
    for (const auto& node : list) {
        if (!node.value("enabled", true)) continue;
        try {
            result.add(render_node(node, builder, component, variables));
        } catch (const mgeom::GeometryError& error) {
            throw BuildError("'" + label_of(node) + "': " + error.what());
        }
    }
    return result.merged();
}

// -- the builder ------------------------------------------------------------------

const Layers& Builder::build(std::string_view component, const Values& params) {
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
    Layers layers = render_list(definition->shapes->json, *this, qualified, variables);
    return cache_.emplace(key, std::move(layers)).first->second;
}

}  // namespace mems
