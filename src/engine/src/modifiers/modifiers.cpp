#include "modifiers.hpp"

#include <cmath>
#include <cstdio>
#include <map>

namespace mems {

ApplyModifier find_modifier(std::string_view kind) {
    static const std::map<std::string, ApplyModifier, std::less<>> modifiers = {
        {"array", apply_array},
        {"polar_array", apply_polar_array},
        {"mirror", apply_mirror},
        {"corners", apply_corners},
    };
    const auto found = modifiers.find(kind);
    return found == modifiers.end() ? nullptr : found->second;
}

int copy_count(const Context& ctx, const Json& modifier, const char* key, double fallback, const char* what) {
    const double number = ctx.number(modifier, key, fallback);
    if (number != std::trunc(number) || number < 0) {
        char buffer[32];
        std::snprintf(buffer, sizeof buffer, "%g", number);
        throw BuildError(std::string(what) + " must be a non-negative integer, got " + buffer);
    }
    return static_cast<int>(number);
}

namespace {

void strings_of(const Json& value, std::vector<std::string>& out) {
    if (value.is_string()) out.push_back(value.get<std::string>());
    else if (value.is_array() || value.is_object())
        for (const auto& item : value) strings_of(item, out);
}

// The self.<point>.<axis> names in the modifier's expressions and ``extra``.
std::vector<std::string> self_names(const Json& modifier, const std::vector<std::string>& extra) {
    std::vector<std::string> texts;
    strings_of(modifier, texts);
    std::vector<std::string> found;
    for (const auto& text : texts) {
        if (text.find("self.") == std::string::npos) continue;
        try {
            for (const auto& name : names_in(text))
                if (name.rfind("self.", 0) == 0 && std::count(name.begin(), name.end(), '.') == 2) found.push_back(name);
        } catch (const ExpressionError&) {
        }
    }
    for (const auto& name : extra)
        if (name.rfind("self.", 0) == 0) found.push_back(name);
    return found;
}

}  // namespace

bool uses_self(const Json& modifier, const std::vector<std::string>& extra_names) {
    return !self_names(modifier, extra_names).empty();
}

Variables with_self(const Json& modifier, const Result& own, const Variables& variables,
                    const std::vector<std::string>& extra_names) {
    const auto names = self_names(modifier, extra_names);
    if (names.empty()) return variables;
    const Points self{"self", bbox_of(own.layers), own.points, {}};
    Variables result = variables;
    for (const auto& name : names) {
        const size_t dot = name.rfind('.');
        const mgeom::Point p = self.point(name.substr(5, dot - 5));
        result[name] = name.substr(dot + 1) == "x" ? p.x : p.y;
    }
    return result;
}

mgeom::Point point_of(const std::string& reference, const Result& own, const Scope& scope, const std::string& what) {
    const std::string node = reference.substr(0, reference.find('.'));
    const std::string point = reference.substr(reference.find('.') + 1);
    if (node == "self") return Points{"self", bbox_of(own.layers), own.points, {}}.point(point);
    const auto found = scope.find(node);
    if (found == scope.end()) throw BuildError(what + ": no shape named '" + node + "' is visible");
    return found->second.point(point);
}

Layers placed(const Layers& layers, const mgeom::Transform& t) {
    Layers result;
    for (const auto& [layer, region] : layers) result.emplace(layer, region.transformed(t));
    return result;
}

}  // namespace mems
