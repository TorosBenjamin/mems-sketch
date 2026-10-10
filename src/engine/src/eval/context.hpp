// What a shape kind needs to render one copy of itself (inside the engine):
// the values its expressions see, the component it is written in (for the
// names its references use) and the builder (to build what they place).
#pragma once

#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "mems/build.hpp"
#include "mems/expression.hpp"

namespace mems {

using Json = nlohmann::ordered_json;

struct Context {
    Builder& builder;
    const std::string& component;  // its unique name
    const Variables& variables;    // parameters, process constants and the array indices

    // The value of a number or an expression in ``node[key]`` (``fallback`` when
    // it is missing).
    double number(const Json& node, const char* key, double fallback) const;
    double number(const Json& node, const char* key) const;
    double value(const Json& value) const;

    // The geometry of a list of child nodes, merged per layer.
    Layers children(const Json& list) const;
};

// Collects regions per layer and unites them once at the end.
class LayerSet {
public:
    void add(const Layers& layers);
    void add(const std::string& layer, const mgeom::Region& region);
    Layers merged() const;

private:
    std::map<std::string, std::vector<mgeom::Region>> parts_;
};

// A list of nodes evaluated with ``variables``, merged per layer.
Layers render_list(const Json& list, Builder& builder, const std::string& component, const Variables& variables);

}  // namespace mems
