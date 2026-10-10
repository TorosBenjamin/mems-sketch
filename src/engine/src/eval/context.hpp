// What a shape kind needs to render one copy of itself (inside the engine):
// the values its expressions see, the points of the named shapes it can see,
// the component it is written in (for the names its references use) and the
// builder (to build what they place).
#pragma once

#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "mems/build.hpp"
#include "mems/expression.hpp"
#include "mgeom/transform.hpp"

namespace mems {

using Json = nlohmann::ordered_json;

// The alignment points of one evaluated node, in the frame of the list holding
// it (mems_sketch.core.shapes.points.NodePoints): declared ones (a reference's)
// and those of its bounding box, mapped by ``transform`` into another frame
// (inside a transform node).
struct Points {
    std::string name;
    mgeom::Box box;     // of its geometry, all layers
    PointMap declared;
    mgeom::Transform transform;

    mgeom::Point point(const std::string& point) const;
    Points seen_through(const mgeom::Transform& t) const;
};

using Scope = std::map<std::string, Points, std::less<>>;

// What one copy of a node makes: geometry, and the points it declares.
struct Result {
    Layers layers;
    PointMap points;
};

struct Context {
    Builder& builder;
    const std::string& component;  // its unique name
    const Variables& variables;    // parameters, process constants and the array indices
    const Scope& scope;            // the named nodes it can see

    // The value of a number or an expression (point coordinates such as
    // beam.right.x come from the scope).
    double value(const Json& value) const;
    double number(const Json& node, const char* key, double fallback) const;
    double number(const Json& node, const char* key) const;

    // Sibling child lists evaluated together (names in one are visible in the
    // others), each merged per layer; ``scope`` replaces the visible points.
    std::vector<Layers> children(const std::vector<const Json*>& lists, const Scope* scope = nullptr) const;
    Layers children(const Json& list) const { return children({&list}).front(); }
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

mgeom::Box bbox_of(const Layers& layers);

// Sibling lists evaluated in the order their alignments need; their geometry,
// merged per list, and the points of their named nodes.
struct Rendered {
    std::vector<Layers> layers;
    Scope local;
};
Rendered render_lists(const std::vector<const Json*>& lists, Builder& builder, const std::string& component,
                      const Variables& variables, const Scope& scope);

}  // namespace mems
