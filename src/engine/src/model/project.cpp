// The project model; see mems/project.hpp. Name resolution, reference checks
// and parameter values follow mems_sketch.core.project, compiler and
// user_component rule for rule, with the same messages.
#include "mems/project.hpp"

#include <algorithm>
#include <cstdio>
#include <cmath>
#include <functional>
#include <set>

#include <nlohmann/json.hpp>

#include "mems/expression.hpp"
#include "shape_tree.hpp"

namespace mems {

using Json = nlohmann::ordered_json;

namespace {

std::string in_quotes(std::string_view s) { return "'" + std::string(s) + "'"; }

// "lib.comb/finger" -> ("lib", "comb/finger"); without a library: (nothing, path).
std::pair<std::optional<std::string>, std::string> split(std::string_view qualified) {
    const size_t dot = qualified.find('.');
    if (dot == std::string_view::npos) return {std::nullopt, std::string(qualified)};
    return {std::string(qualified.substr(0, dot)), std::string(qualified.substr(dot + 1))};
}

std::string owner_of(std::string_view path) {
    const size_t slash = path.rfind('/');
    return slash == std::string_view::npos ? std::string() : std::string(path.substr(0, slash));
}

// A private component is seen only from inside its owner (``where`` nothing:
// from outside every component, where anything may be named).
void check_visible(std::string_view qualified, std::string_view path, const std::optional<std::string>& where) {
    const std::string owner = owner_of(path);
    if (owner.empty() || !where || *where == owner || where->rfind(owner + "/", 0) == 0) return;
    throw PrivateComponent(in_quotes(qualified) + " is private to " + in_quotes(owner) +
                           ": it can only be placed inside " + in_quotes(owner));
}

Value value_of(const Json& j) {
    if (j.is_string()) return j.get<std::string>();
    if (j.is_number()) return j.get<double>();
    throw ModelError("a value is neither a number nor an expression: " + j.dump());
}

std::optional<Value> optional_value(const Json& j, const char* key) {
    if (!j.contains(key) || j[key].is_null()) return std::nullopt;
    return value_of(j[key]);
}

// The components the ref shapes in ``node`` name, wherever they are nested.
void collect_references(const Json& node, std::set<std::string>& found) {
    if (node.is_object()) {
        const auto kind = node.find("kind");
        if (kind != node.end() && kind->is_string() && *kind == "ref") {
            const auto component = node.find("component");
            if (component != node.end() && component->is_string()) found.insert(component->get<std::string>());
        }
        for (const auto& [key, value] : node.items()) collect_references(value, found);
    } else if (node.is_array()) {
        for (const auto& item : node) collect_references(item, found);
    }
}

ComponentDef component_from(const Json& j) {
    ComponentDef def;
    def.name = j.at("name").get<std::string>();
    if (j.contains("level") && !j["level"].is_null()) def.level = j["level"].get<std::string>();
    const Json parameters = j.value("parameters", Json::array());
    for (const auto& p : parameters) {
        ParamDef param;
        param.name = p.at("name").get<std::string>();
        param.default_value = p.contains("default") ? value_of(p["default"]) : Value{0.0};
        param.min = optional_value(p, "min");
        param.max = optional_value(p, "max");
        param.min_exclusive = p.value("min_exclusive", false);
        param.max_exclusive = p.value("max_exclusive", false);
        param.integer = p.value("integer", false);
        param.internal = p.value("internal", false);
        def.parameters.push_back(std::move(param));
    }
    std::set<std::string> references;
    collect_references(j.value("shapes", Json::array()), references);
    def.references.assign(references.begin(), references.end());
    Json canonical = j;
    canonical.erase("waivers");  // they accept violations; the geometry does not depend on them
    def.canonical = canonical.dump();
    def.shapes = std::make_shared<const ShapeTree>(ShapeTree{j.value("shapes", Json::array()), j.value("points", Json::array())});
    return def;
}

std::string hash_of(std::initializer_list<std::string_view> parts, const std::vector<std::string>& more = {}) {
    std::string text;
    for (auto part : parts) {
        text += part;
        text += '\0';
    }
    for (const auto& part : more) {
        text += part;
        text += '\0';
    }
    return sha256(text);
}

std::string format_number(double v) {
    char buffer[32];
    std::snprintf(buffer, sizeof buffer, "%g", v);
    return buffer;
}

}  // namespace

// -- reading ------------------------------------------------------------------------

Project Project::from_json(std::string_view text) {
    Json j;
    try {
        j = Json::parse(text);
    } catch (const nlohmann::json::exception& error) {
        throw ModelError(std::string("not a project: ") + error.what());
    }
    Project p;
    p.name_ = j.value("name", "untitled");
    if (j.contains("top") && !j["top"].is_null()) p.top_ = j["top"].get<std::string>();
    // (Each section is a copy: kept in a variable, so that it outlives the loop.)
    const Json process = j.value("process", Json::object());
    const Json constants = process.value("constants", Json::object());
    for (const auto& [name, value] : constants.items()) p.constants_.emplace_back(name, value_of(value));
    const Json levels = process.value("levels", Json::array());
    for (const auto& entry : levels) {
        Level level{entry.at("layer").get<std::string>(), {}};
        const Json roles = entry.value("roles", Json::object());
        for (const auto& [role, layer] : roles.items()) level.roles[role] = layer.get<std::string>();
        p.levels_.push_back(std::move(level));
    }
    if (process.contains("default_level") && !process["default_level"].is_null())
        p.default_level_ = process["default_level"].get<std::string>();
    const Json components = j.value("components", Json::object());
    for (const auto& [name, def] : components.items()) {
        p.local_.order.push_back(name);
        p.local_.components[name] = component_from(def);
    }
    const Json libraries = j.value("libraries", Json::object());
    for (const auto& [library, members] : libraries.items()) {
        Library& pool = p.libraries_[library];
        p.library_order_.push_back(library);
        for (const auto& [name, def] : members.items()) {
            pool.order.push_back(name);
            pool.components[name] = component_from(def);
        }
    }
    const Json imports = j.value("imports", Json::object());
    for (const auto& [name, cell] : imports.items()) {
        // Its layers sorted, as Python's fingerprint has them.
        std::map<std::string, std::string> layers = cell.value("layers", std::map<std::string, std::string>{});
        p.imports_[name] = {cell.value("digest", ""), cell.value("cell", ""), Json(layers).dump()};
    }
    const Json builtins = j.value("builtins", Json::object());
    for (const auto& [name, version] : builtins.items())
        p.builtins_[name] = version.get<std::string>();
    return p;
}

// -- process constants ------------------------------------------------------------------

const std::map<std::string, double>& Project::scope() const {
    if (!scope_) {
        std::vector<std::pair<std::string, Definition>> definitions;
        for (const auto& [name, value] : constants_) {
            if (const double* number = std::get_if<double>(&value)) definitions.emplace_back(name, *number);
            else definitions.emplace_back(name, std::get<std::string>(value));
        }
        auto resolved = std::make_shared<std::map<std::string, double>>();
        for (const auto& [name, value] : resolve_variables(definitions)) (*resolved)["process." + name] = value;
        scope_ = resolved;
    }
    return *scope_;
}

// -- names ---------------------------------------------------------------------------

const Project::Library& Project::pool(const std::optional<std::string>& library) const {
    if (!library) return local_;
    const auto found = libraries_.find(*library);
    if (found == libraries_.end()) throw UnknownComponent("unknown component library " + in_quotes(*library));
    return found->second;
}

std::string Project::qualify(std::string_view name, const std::optional<std::string>& context) const {
    // nothing: from outside (anything may be named); "": at project level
    std::optional<std::string> library, where;
    if (context) std::tie(library, where) = split(*context);
    if (name.find('.') != std::string_view::npos) {
        const auto [target, path] = split(name);
        const auto found = libraries_.find(*target);
        if (found == libraries_.end() || !found->second.components.count(path))
            throw UnknownComponent("unknown component " + in_quotes(name));
        std::optional<std::string> seen_from = (library == target || !context) ? where : std::optional<std::string>("");
        check_visible(name, path, seen_from);
        return std::string(name);
    }
    const Library& here = pool(library);
    const std::string prefix = library ? *library + "." : "";
    if (name.find('/') != std::string_view::npos) {
        if (!here.components.count(name)) throw UnknownComponent("unknown component " + in_quotes(name));
        check_visible(prefix + std::string(name), name, where);
        return prefix + std::string(name);
    }
    std::string scope = where.value_or("");
    while (true) {
        const std::string candidate = scope.empty() ? std::string(name) : scope + "/" + std::string(name);
        if (here.components.count(candidate)) return prefix + candidate;
        if (scope.empty()) break;
        scope = owner_of(scope);
    }
    if (imports_.count(name) || builtins_.count(name)) return std::string(name);
    throw UnknownComponent("unknown component " + in_quotes(name));
}

const ComponentDef* Project::definition(std::string_view qualified) const {
    const auto [library, path] = split(qualified);
    const Library& here = pool(library);
    const auto found = here.components.find(path);
    return found == here.components.end() ? nullptr : &found->second;
}

void Project::check_references() const {
    std::map<std::string, int> state;  // 1: visiting, 2: done
    std::function<void(const std::string&, std::vector<std::string>&)> visit =
        [&](const std::string& qualified, std::vector<std::string>& path) {
            if (state[qualified] == 2) return;
            if (state[qualified] == 1) {
                std::string cycle;
                for (const auto& step : path) cycle += step + " -> ";
                throw ModelError("circular component reference: " + cycle + qualified);
            }
            const ComponentDef* def = definition(qualified);
            if (!def) return;  // built-in or imported
            state[qualified] = 1;
            path.push_back(qualified);
            for (const auto& reference : def->references) {
                std::string target;
                try {
                    target = qualify(reference, qualified);
                } catch (const PrivateComponent& error) {
                    throw ModelError("component " + in_quotes(qualified) + ": " + error.what());
                } catch (const UnknownComponent&) {
                    throw ModelError("component " + in_quotes(qualified) + " references unknown component " +
                                     in_quotes(reference));
                }
                visit(target, path);
            }
            path.pop_back();
            state[qualified] = 2;
        };
    auto check_pool = [&](const std::optional<std::string>& library) {
        const Library& here = pool(library);
        const std::string prefix = library ? *library + "." : "";
        for (const auto& name : here.order) {
            const std::string owner = owner_of(name);
            if (!owner.empty() && !here.components.count(owner))
                throw ModelError("component " + in_quotes(prefix + name) + " is private to a missing " + in_quotes(owner));
            std::vector<std::string> path;
            visit(prefix + name, path);
        }
    };
    check_pool(std::nullopt);
    for (const auto& library : library_order_) check_pool(library);
}

// -- parameters -----------------------------------------------------------------------------

std::map<std::string, double> Project::variables(std::string_view component, const Values& given) const {
    const std::string qualified = qualify(component);
    const ComponentDef* def = definition(qualified);
    if (!def) throw ModelError(in_quotes(qualified) + " is not built by the engine yet: built-in and imported components are Python's");
    const auto& constants = scope();
    Variables known(constants.begin(), constants.end());

    // Given values: numbers, or expressions over the process constants.
    std::map<std::string, double> values;
    for (const auto& [name, value] : given) {
        values[name] = std::holds_alternative<double>(value)
                           ? std::get<double>(value)
                           : Expression(std::get<std::string>(value)).evaluate(known);
    }
    // Defaults for the others, which may use the given values and each other.
    std::vector<std::pair<std::string, Definition>> missing;
    for (const auto& param : def->parameters) {
        if (values.count(param.name)) continue;
        if (const double* number = std::get_if<double>(&param.default_value)) missing.emplace_back(param.name, *number);
        else missing.emplace_back(param.name, std::get<std::string>(param.default_value));
    }
    Variables fixed = known;
    for (const auto& [name, value] : values) fixed[name] = value;
    for (const auto& [name, value] : resolve_variables(missing, fixed)) values[name] = value;

    std::set<std::string> declared;
    for (const auto& param : def->parameters) declared.insert(param.name);
    for (const auto& [name, value] : values) {
        if (!declared.count(name))
            throw ModelError("component " + in_quotes(qualified) + " has no parameter " + in_quotes(name));
    }
    Variables all = known;  // what limits see: the constants and every value
    for (const auto& [name, value] : values) all[name] = value;
    auto limit = [&](const std::optional<Value>& v) -> std::optional<double> {
        if (!v) return std::nullopt;
        if (const double* number = std::get_if<double>(&*v)) return *number;
        return Expression(std::get<std::string>(*v)).evaluate(all);
    };
    std::map<std::string, double> result(constants.begin(), constants.end());
    for (const auto& param : def->parameters) {
        const double v = values.at(param.name);
        const std::string what = "parameter " + in_quotes(param.name) + " of " + in_quotes(qualified);
        if (param.integer && !(std::isfinite(v) && v == std::trunc(v)))
            throw ModelError(what + " must be an integer, not " + format_number(v));
        if (const auto low = limit(param.min); low && !(param.min_exclusive ? v > *low : v >= *low))
            throw ModelError(what + (param.min_exclusive ? " must be more than " : " must be at least ") +
                             format_number(*low) + ", not " + format_number(v));
        if (const auto high = limit(param.max); high && !(param.max_exclusive ? v < *high : v <= *high))
            throw ModelError(what + (param.max_exclusive ? " must be less than " : " must be at most ") +
                             format_number(*high) + ", not " + format_number(v));
        result[param.name] = v + 0.0;
    }
    return result;
}

// -- fingerprints ------------------------------------------------------------------------------

std::string Project::fingerprint(std::string_view qualified) const {
    if (!fingerprints_) fingerprints_ = std::make_shared<std::map<std::string, std::string>>();
    std::vector<std::string> visiting;
    return fingerprint(std::string(qualified), visiting);
}

std::string Project::fingerprint(const std::string& qualified, std::vector<std::string>& visiting) const {
    if (const auto found = fingerprints_->find(qualified); found != fingerprints_->end()) return found->second;
    if (std::find(visiting.begin(), visiting.end(), qualified) != visiting.end())
        throw ModelError("circular component reference involving " + in_quotes(qualified));
    std::string digest;
    if (const auto imported = imports_.find(qualified); imported != imports_.end()) {
        digest = hash_of({"import", imported->second.digest, imported->second.cell, imported->second.layers});
    } else if (const ComponentDef* def = definition(qualified)) {
        visiting.push_back(qualified);
        std::vector<std::string> children;
        for (const auto& reference : def->references)
            children.push_back(fingerprint(qualify(reference, qualified), visiting));
        visiting.pop_back();
        std::sort(children.begin(), children.end());
        digest = hash_of({"user", def->canonical}, children);
    } else if (const auto builtin = builtins_.find(qualified); builtin != builtins_.end()) {
        digest = hash_of({"builtin", qualified, builtin->second});
    } else {
        throw UnknownComponent("unknown component " + in_quotes(qualified));
    }
    (*fingerprints_)[qualified] = digest;
    return digest;
}

}  // namespace mems
