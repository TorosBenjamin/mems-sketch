// Building components: a project's shape trees evaluated into regions of the
// geometry library, per layer, the way mems_sketch.core.shapes evaluates
// them (the equivalence test, tests/test_engine_build.py, checks it).
//
// Every shape kind (rect, polygon, circle, arc, path, guide, boolean,
// transform, offset, fillet, layer_map, references to user components), every
// modifier (array, polar_array, mirror, corners), alignment, point
// coordinates in expressions, components' declared points and levels of the
// layer stack (core-architecture.md, step 6). Built-in components are still Python's:
// placing one raises NotSupported, so callers can fall back to the Python
// backend.
#pragma once

#include <map>
#include <string>
#include <string_view>

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

// A built component: its geometry per layer, merged, and its declared points.
struct Built {
    Layers layers;
    PointMap points;
};

// Builds the components of one project. Each component built with the same
// parameter values is built once (by fingerprint and values).
class Builder {
public:
    explicit Builder(const Project& project) : project_(project) {}

    // A component (as written at project level) with the given parameter values,
    // defaults for the rest, on its own level of the layer stack (its default
    // level, else the process's).
    const Built& build(std::string_view component, const Values& params = {});

    // A component by unique name on a given level (or none: no layer stack).
    const Built& build_on(const std::string& qualified, const Values& params, const OptionalLevel& level);

    const Project& project() const { return project_; }

    // The level of the component being built, and the layer a shape's ``layer``
    // names on it (``level-1``, ``level.anchor``, ``metal``).
    const OptionalLevel& level() const { return level_; }
    std::string layer(const std::string& spec) const { return project_.layer(spec, level_); }

private:
    const Project& project_;
    std::map<std::string, Built> cache_;
    OptionalLevel level_;  // while a component is built
};

}  // namespace mems
