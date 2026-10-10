// Building components: a project's shape trees evaluated into regions of the
// geometry library, per layer, the way mems_sketch.core.shapes evaluates
// them (the equivalence test, tests/test_engine_build.py, checks it).
//
// The kinds come one at a time (core-architecture.md, step 6). So far: rect,
// polygon, circle, boolean, transform (and group), references to user
// components, and the array modifier. Anything else raises NotSupported, so
// callers can fall back to the Python backend.
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

// Builds the components of one project. Each component built with the same
// parameter values is built once (by fingerprint and values).
class Builder {
public:
    explicit Builder(const Project& project) : project_(project) {}

    // A component (as written at project level) with the given parameter values,
    // defaults for the rest: its geometry per layer, merged.
    const Layers& build(std::string_view component, const Values& params = {});

    const Project& project() const { return project_; }

private:
    const Project& project_;
    std::map<std::string, Layers> cache_;
};

}  // namespace mems
