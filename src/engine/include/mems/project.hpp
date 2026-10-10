// The project model: what the engine knows of a project before building it.
// The same rules as mems_sketch.core.project, compiler and user_component:
// process constants, component name resolution (private components,
// libraries, imports, built-ins), reference checks, parameter values and
// fingerprints.
//
// A project arrives as JSON, made by mems_sketch.engine.project_data from the
// pydantic model (which has validated it):
//
//   {"name": ..., "top": "top" | null,
//    "process": {"constants": {"gap": 2, "pitch": "2 * gap"}},
//    "components": {"top": <ComponentDef>, "comb/finger": <ComponentDef>},
//    "libraries": {"std": {"anchor": <ComponentDef>}},
//    "imports": {"pads": {"digest": ..., "cell": ..., "layers": {...}}},
//    "builtins": {"comb_drive": "<a hash of its code>"}}
//
// where a ComponentDef is its model_dump(mode="json") without waivers.
#pragma once

#include <map>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <variant>
#include <vector>

namespace mems {

// The project or a value in it is wrong (Python: ValueError).
class ModelError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

// A name that names no component (Python: KeyError).
class UnknownComponent : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

// A private component named from outside its owner (a kind of unknown name).
class PrivateComponent : public UnknownComponent {
public:
    using UnknownComponent::UnknownComponent;
};

// A parameter value: a number, or an expression over the process constants.
using Value = std::variant<double, std::string>;
using Values = std::vector<std::pair<std::string, Value>>;

struct ParamDef {
    std::string name;
    Value default_value = 0.0;
    std::optional<double> min, max;
    bool integer = false;
    bool internal = false;
};

struct ShapeTree;  // a component's shapes as read (see src/model/shape_tree.hpp)

struct ComponentDef {
    std::string name;  // its path: "plate", "comb/finger"
    std::vector<ParamDef> parameters;
    std::vector<std::string> references;  // the components its ref shapes name, as written, sorted
    std::string canonical;                // its definition as canonical JSON (for the fingerprint)
    std::shared_ptr<const ShapeTree> shapes;
};

class Project {
public:
    static Project from_json(std::string_view json);

    const std::string& name() const { return name_; }
    const std::optional<std::string>& top() const { return top_; }

    // The resolved process constants, as expressions see them: "process.gap".
    // Throws when the constants do not resolve.
    const std::map<std::string, double>& scope() const;

    // The unique name of the component ``name`` refers to, written in the
    // component ``context`` (a unique name; "" at project level; nothing: from
    // outside every component, where any component can be named).
    std::string qualify(std::string_view name, const std::optional<std::string>& context = std::nullopt) const;

    // A user component's definition by unique name ("plate", "comb/finger",
    // "std.anchor"), or nothing for a built-in or imported one.
    const ComponentDef* definition(std::string_view qualified) const;

    // Every reference resolves and sees what it places, private components have
    // their owner, and references form no cycle; else ModelError.
    void check_references() const;

    // A user component's parameter values: those given (numbers, or expressions
    // over the process constants), defaults for the rest (which may use the
    // others), checked against min, max and integer; with the process constants.
    std::map<std::string, double> variables(std::string_view component, const Values& given = {}) const;

    // A hash of everything a component's geometry depends on except its
    // parameter values and the process constants: its definition and,
    // recursively, those of what it places.
    std::string fingerprint(std::string_view qualified) const;

private:
    struct Library {
        std::vector<std::string> order;
        std::map<std::string, ComponentDef, std::less<>> components;
    };
    struct Import {
        std::string digest, cell, layers;
    };

    const Library& pool(const std::optional<std::string>& library) const;
    std::string fingerprint(const std::string& qualified, std::vector<std::string>& visiting) const;

    std::string name_;
    std::optional<std::string> top_;
    std::vector<std::pair<std::string, Value>> constants_;
    Library local_;
    std::vector<std::string> library_order_;
    std::map<std::string, Library, std::less<>> libraries_;
    std::map<std::string, Import, std::less<>> imports_;
    std::map<std::string, std::string, std::less<>> builtins_;

    mutable std::shared_ptr<std::map<std::string, double>> scope_;
    mutable std::shared_ptr<std::map<std::string, std::string>> fingerprints_;
};

// SHA-256 of ``data``, in hex.
std::string sha256(std::string_view data);

}  // namespace mems
