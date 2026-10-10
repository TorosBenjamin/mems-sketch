// Building components: a project's shape trees evaluated into regions of the
// geometry library, per layer, the way mems_sketch.core.shapes evaluates
// them (the equivalence test, tests/test_engine_build.py, checks it).
//
// Every shape kind (rect, polygon, circle, arc, path, guide, boolean,
// transform, offset, fillet, layer_map, references to user components), every
// modifier (array, polar_array, mirror, corners), alignment, point
// coordinates in expressions, components' declared points and levels of the
// layer stack (core-architecture.md, step 6). Built-in components are built
// from their definitions like any other; imported cells come as polygons.
// What a component places whole stays a placed instance of what was built
// (Built::instances); operations that work on geometry (booleans, offsets,
// fillets, layer maps, rounded corners) flatten what they are given.
#pragma once

#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "mems/project.hpp"
#include "mgeom/region.hpp"

namespace mems {

// A shape tree that does not build (Python: ValueError).
class BuildError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

// Something the engine does not build yet: a kind, a modifier, alignment...
class NotSupported : public BuildError {
public:
    using BuildError::BuildError;
};

using Layers = std::map<std::string, mgeom::Region, std::less<>>;
using PointMap = std::map<std::string, mgeom::Point, std::less<>>;

struct Built;

// A component placed whole (a reference, maybe copied by modifiers, turned by
// transforms): what was built, and where it goes. Components keep what they
// place as instances, so each is built once however often it is placed, and
// whoever draws it can draw it once.
struct Instance {
    std::shared_ptr<const Built> built;
    mgeom::Transform transform;
};
using Instances = std::vector<Instance>;

// A built component: its own geometry per layer, merged (everything but what
// it places whole), the components it places whole, and its declared points.
struct Built {
    std::string key;  // what the build cache knows it by: the same key, the same geometry
    Layers layers;
    Instances instances;
    PointMap points;
    mgeom::Box box;  // around everything, instances included

    // Everything, instances placed and merged in, per layer (worked out once).
    const Layers& flat() const;

private:
    mutable std::once_flag flat_once_;
    mutable Layers flat_;
};

// Geometry with what it places whole merged in.
Layers flatten(const Layers& own, const Instances& instances);

// The box around a built component where an instance places it.
mgeom::Box box_of(const Instance& instance);

// The box around geometry and instances together.
mgeom::Box bbox_of(const Layers& layers, const Instances& instances);

// Where a node is in a shape tree: for each level, which of its parent's child
// lists (0 at the top) and its index there (mems_sketch.core.shapes.NodePath).
using NodePath = std::vector<std::pair<int, int>>;

// One node as evaluated (its first copy, when repeated), in the frame of the
// list holding it: its geometry and points, the frame its children are in
// (``inner``), and the move its alignment made (``shift``).
struct NodeRecord {
    Layers layers;
    Instances instances;
    std::string name;  // its name, or its kind
    PointMap declared;
    mgeom::Transform inner, shift;
};
using Records = std::map<NodePath, NodeRecord>;

// Built components by a key of everything they depend on (fingerprint, values,
// level, layer stack), so one cache serves every version of a project: after
// an edit only what the edit changed is built again.
class BuildCache {
public:
    explicit BuildCache(size_t max_entries = 4096) : max_entries_(max_entries) {}
    std::shared_ptr<const Built> find(const std::string& key) const;
    std::shared_ptr<const Built> put(std::shared_ptr<const Built> built);
    void clear() { built_.clear(); }
    size_t size() const { return built_.size(); }

private:
    size_t max_entries_;
    std::map<std::string, std::shared_ptr<const Built>> built_;
};

// Builds the components of one project. Each component built with the same
// parameter values is built once (by fingerprint and values).
class Builder {
public:
    explicit Builder(const Project& project, std::shared_ptr<BuildCache> cache = std::make_shared<BuildCache>())
        : project_(project), cache_(std::move(cache)) {}

    // A component (as written at project level) with the given parameter values,
    // defaults for the rest, on its own level of the layer stack (its default
    // level, else the process's).
    std::shared_ptr<const Built> build(std::string_view component, const Values& params = {});

    // A component by unique name on a given level (or none: no layer stack).
    std::shared_ptr<const Built> build_on(const std::string& qualified, const Values& params,
                                          const OptionalLevel& level);

    // Every node of a component's own shape tree as evaluated, by path: what
    // the editor shows and moves. When evaluating fails, the nodes evaluated
    // so far, and the error in ``error``.
    Records records(std::string_view component, const Values& params, std::string* error = nullptr);

    const Project& project() const { return project_; }

    // The level of the component being built, and the layer a shape's ``layer``
    // names on it (``level-1``, ``level.anchor``, ``metal``).
    const OptionalLevel& level() const { return level_; }
    std::string layer(const std::string& spec) const { return project_.layer(spec, level_); }

    // While records are made: the path of the node being evaluated, and where
    // nodes are recorded (none inside the components it places).
    NodePath& path() { return path_; }
    Records* recording() { return recording_; }

private:
    const Project& project_;
    std::shared_ptr<BuildCache> cache_;
    OptionalLevel level_;  // while a component is built
    NodePath path_;
    Records* recording_ = nullptr;
    std::map<std::string, std::shared_ptr<const Built>, std::less<>> imported_;
};

}  // namespace mems
