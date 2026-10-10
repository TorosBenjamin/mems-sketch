// Building components: a project's shape trees evaluated into regions of the
// geometry library, per layer, the way mems_sketch.core.shapes evaluates
// them (the equivalence test, tests/test_engine_build.py, checks it).
//
// The kinds come one at a time (core-architecture.md, step 6). So far every
// shape kind (rect, polygon, circle, arc, path, guide, boolean, transform,
// offset, fillet, layer_map, references to user components), the array
// modifier, alignment, point coordinates in expressions and components'
// declared points. Anything else (the other modifiers, built-in components)
// raises NotSupported, so callers can fall back to the Python backend.
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
    // defaults for the rest.
    const Built& build(std::string_view component, const Values& params = {});

    const Project& project() const { return project_; }

private:
    const Project& project_;
    std::map<std::string, Built> cache_;
};

}  // namespace mems
