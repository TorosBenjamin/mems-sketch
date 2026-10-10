// The layer stack: levels of components and layers relative to them, the same
// rules and messages as mems_sketch.core.levels.
#include <cctype>
#include <optional>
#include <string>

#include "mems/project.hpp"

namespace mems {

namespace {

// ``level``, ``level+1``, ``level-2.anchor``: levels up (+) or down, and a role.
struct Relative {
    int offset = 0;
    std::string role;
};

std::optional<Relative> relative(const std::string& spec) {
    const std::string word = "level";
    if (spec.compare(0, word.size(), word) != 0) return std::nullopt;
    size_t k = word.size();
    Relative result;
    if (k < spec.size() && (spec[k] == '+' || spec[k] == '-')) {
        const int sign = spec[k] == '+' ? 1 : -1;
        const size_t digits = ++k;
        while (k < spec.size() && std::isdigit(static_cast<unsigned char>(spec[k]))) ++k;
        if (k == digits) return std::nullopt;
        result.offset = sign * std::stoi(spec.substr(digits, k - digits));
    }
    if (k < spec.size()) {
        if (spec[k] != '.' || k + 1 == spec.size()) return std::nullopt;
        const std::string role = spec.substr(k + 1);
        if (!(std::isalpha(static_cast<unsigned char>(role[0])) || role[0] == '_')) return std::nullopt;
        for (char c : role)
            if (!(std::isalnum(static_cast<unsigned char>(c)) || c == '_')) return std::nullopt;
        result.role = role;
    }
    return result;
}

std::string in_quotes(const std::string& text) { return "'" + text + "'"; }

size_t index_of(const std::vector<Level>& levels, const std::string& level) {
    for (size_t k = 0; k < levels.size(); ++k)
        if (levels[k].layer == level) return k;
    throw ModelError(in_quotes(level) + " is not a level of the layer stack");
}

size_t offset_level(const std::vector<Level>& levels, const std::string& spec, const Relative& r,
                    const OptionalLevel& current) {
    if (!current) {
        if (levels.empty()) throw ModelError(in_quotes(spec) + " needs a layer stack, and the process has none");
        throw ModelError(in_quotes(spec) + " needs the component to be on a level of the layer stack");
    }
    const long target = static_cast<long>(index_of(levels, *current)) + r.offset;
    if (target < 0)
        throw ModelError(in_quotes(spec) + " on " + in_quotes(*current) + " is below the bottom of the layer stack");
    if (target >= static_cast<long>(levels.size()))
        throw ModelError(in_quotes(spec) + " on " + in_quotes(*current) + " is above the top of the layer stack");
    return static_cast<size_t>(target);
}

}  // namespace

OptionalLevel Project::top_level(const OptionalLevel& own) const {
    if (own) {
        index_of(levels_, *own);
        return own;
    }
    if (default_level_) return default_level_;
    if (!levels_.empty()) return levels_.front().layer;
    return std::nullopt;
}

OptionalLevel Project::place(const OptionalLevel& spec, const OptionalLevel& own, const OptionalLevel& current) const {
    if (!spec) {
        if (!own) return current;
        index_of(levels_, *own);
        return own;
    }
    const auto r = relative(*spec);
    if (!r) {
        index_of(levels_, *spec);
        return spec;
    }
    if (!r->role.empty()) throw ModelError("a component is placed on a level, not on a role: " + in_quotes(*spec));
    return levels_[offset_level(levels_, *spec, *r, current)].layer;
}

std::string Project::layer(const std::string& spec, const OptionalLevel& current) const {
    const auto r = relative(spec);
    if (!r) return spec;
    const Level& level = levels_[offset_level(levels_, spec, *r, current)];
    if (r->role.empty()) return level.layer;
    const auto found = level.roles.find(r->role);
    if (found == level.roles.end())
        throw ModelError("level " + in_quotes(level.layer) + " has no " + in_quotes(r->role) + " layer (for " +
                         in_quotes(spec) + ")");
    return found->second;
}

}  // namespace mems
