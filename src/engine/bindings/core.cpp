// mems_sketch._core: the mems-sketch engine for Python. So far its expressions,
// which give the same values as mems_sketch.core.expressions, and its project
// model (mems_sketch.engine.project_data makes the JSON it reads).
#include <algorithm>
#include <cstdint>
#include <tuple>
#include <map>

#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <nanobind/stl/map.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/set.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/string_view.h>
#include <nanobind/stl/variant.h>
#include <nanobind/stl/vector.h>

#include <nanobind/stl/optional.h>

#include "mems/build.hpp"
#include "mems/expression.hpp"
#include "mems/project.hpp"
#include "mgeom/snap.hpp"

namespace nb = nanobind;
using namespace nb::literals;

namespace {

// Variables from a Python mapping; ints stay ints, as they do in Python.
mems::Lookup lookup_in(const nb::object& variables) {
    return [variables](std::string_view name) -> std::optional<mems::Number> {
        nb::str key(name.data(), name.size());
        if (!nb::bool_(variables.attr("__contains__")(key))) return std::nullopt;
        nb::object value = variables[key];
        if (nb::isinstance<nb::bool_>(value)) throw mems::ExpressionError("a variable is not a number");
        if (nb::isinstance<nb::int_>(value)) return mems::Number::of_int(nb::cast<double>(value));
        return mems::Number::of_float(nb::cast<double>(value));
    };
}

double evaluate(const nb::object& expression, const nb::object& variables) {
    if (nb::isinstance<nb::float_>(expression) || nb::isinstance<nb::int_>(expression))
        return nb::cast<double>(expression);
    const std::string text = nb::cast<std::string>(expression);
    return mems::Expression(text).evaluate(lookup_in(variables));
}

// A layer's region on a grid: ([(hull, [holes])], (left, bottom, right, top)),
// each ring an (n, 2) int64 array of points in grid units, the box around them
// in grid units too (None when the layer is empty).
nb::tuple polygons_tuple(const mgeom::Snapped& snapped);

nb::tuple snapped_polygons(const mgeom::Region& region, double grid, double chord) {
    return polygons_tuple(mgeom::snap(region, grid, chord));
}

nb::tuple polygons_tuple(const mgeom::Snapped& snapped) {
    nb::list polygons;
    std::int64_t left = 0, bottom = 0, right = 0, top = 0;
    bool any = false;
    auto ring = [](const mgeom::GridRing& points) {
        auto* data = new std::int64_t[points.size() * 2];
        for (size_t k = 0; k < points.size(); ++k) {
            data[2 * k] = points[k].x;
            data[2 * k + 1] = points[k].y;
        }
        nb::capsule owner(data, [](void* p) noexcept { delete[] static_cast<std::int64_t*>(p); });
        return nb::ndarray<nb::numpy, std::int64_t, nb::shape<-1, 2>>(data, {points.size(), 2}, owner);
    };
    for (const auto& polygon : snapped.polygons) {
        for (const auto& p : polygon.hull) {
            if (!any) {
                left = right = p.x;
                bottom = top = p.y;
                any = true;
            }
            left = std::min(left, p.x), right = std::max(right, p.x);
            bottom = std::min(bottom, p.y), top = std::max(top, p.y);
        }
        nb::list holes;
        for (const auto& hole : polygon.holes) holes.append(ring(hole));
        polygons.append(nb::make_tuple(ring(polygon.hull), holes));
    }
    nb::object box = any ? nb::object(nb::make_tuple(left, bottom, right, top)) : nb::none();
    return nb::make_tuple(polygons, box);
}

// Snapped regions by region, grid and chord: built components come from the
// engine's cache as the same regions, so each is snapped once. The entries
// keep their regions alive, so an identity is not reused while it is a key.
struct SnapKey {
    const void* region;
    double grid, chord;
    friend bool operator<(const SnapKey& a, const SnapKey& b) {
        return std::tie(a.region, a.grid, a.chord) < std::tie(b.region, b.grid, b.chord);
    }
};
struct SnapEntry {
    mgeom::Region region;
    nb::object result;
};
std::map<SnapKey, SnapEntry>& snap_cache() {
    static auto* cache = new std::map<SnapKey, SnapEntry>();  // never destroyed: holds Python objects
    return *cache;
}

nb::tuple grid_polygons(const mgeom::Region& region, double grid, double chord) {
    auto& cache = snap_cache();
    const SnapKey key{region.identity(), grid, chord};
    if (auto found = cache.find(key); found != cache.end()) return nb::borrow<nb::tuple>(found->second.result);
    nb::tuple result = snapped_polygons(region, grid, chord);
    if (cache.size() >= 2048) cache.clear();
    cache.emplace(key, SnapEntry{region, result});
    return result;
}

nb::tuple transform_tuple(const mgeom::Transform& t) {
    return nb::make_tuple(t.dx, t.dy, t.angle_deg, t.mirror_x, t.scale);
}

nb::dict layers_dict(const mems::Layers& layers, double grid, double chord) {
    nb::dict result;
    for (const auto& [layer, region] : layers) result[nb::str(layer.c_str())] = grid_polygons(region, grid, chord);
    return result;
}

// Instances as [(cell key, transform)], with every cell they reach added to
// ``cells``: {key: (own layers, instances)}, each cell once.
nb::list instances_list(const mems::Instances& instances, nb::dict& cells, double grid, double chord);

void add_cell(const mems::Built& built, nb::dict& cells, double grid, double chord) {
    nb::str key(built.key.c_str());
    if (cells.contains(key)) return;
    cells[key] = nb::none();  // placed in itself never: a placeholder is enough
    nb::list placed = instances_list(built.instances, cells, grid, chord);
    cells[key] = nb::make_tuple(layers_dict(built.layers, grid, chord), placed);
}

nb::list instances_list(const mems::Instances& instances, nb::dict& cells, double grid, double chord) {
    nb::list result;
    for (const auto& instance : instances) {
        add_cell(*instance.built, cells, grid, chord);
        result.append(nb::make_tuple(nb::str(instance.built->key.c_str()), transform_tuple(instance.transform)));
    }
    return result;
}

nb::dict points_dict(const mems::PointMap& points) {
    nb::dict result;
    for (const auto& [name, point] : points) result[nb::str(name.c_str())] = nb::make_tuple(point.x, point.y);
    return result;
}

const char* change_name(mgeom::SnapChange change) {
    switch (change) {
        case mgeom::SnapChange::vanished: return "vanished";
        case mgeom::SnapChange::split: return "split";
        case mgeom::SnapChange::merged: return "merged";
        case mgeom::SnapChange::hole_closed: return "hole_closed";
        case mgeom::SnapChange::hole_joined: return "hole_joined";
        case mgeom::SnapChange::hole_formed: return "hole_formed";
    }
    return "changed";
}

// What snapping changed: (exact area, snapped area, [(change, (x0, y0, x1, y1))]), µm.
nb::tuple report_tuple(const mgeom::SnapReport& report) {
    nb::list events;
    for (const auto& event : report.events)
        events.append(nb::make_tuple(nb::str(change_name(event.change)),
                                     nb::make_tuple(event.where.x0, event.where.y0, event.where.x1, event.where.y1)));
    return nb::make_tuple(report.area_exact, report.area_snapped, events);
}

// A project loaded for building, with a build cache it may share with other
// versions of the project (mems_sketch.engine.Engine).
struct Engine {
    std::shared_ptr<mems::BuildCache> cache;
    std::shared_ptr<const mems::Project> project;
    std::unique_ptr<mems::Builder> builder;

    Engine(std::shared_ptr<mems::BuildCache> c, std::string_view json)
        : cache(std::move(c)),
          project(std::make_shared<const mems::Project>(mems::Project::from_json(json))),
          builder(std::make_unique<mems::Builder>(*project, cache)) {}
};

// Parameter values from a Python mapping, in its order: numbers or expressions.
mems::Values values_in(const nb::dict& given) {
    mems::Values values;
    for (auto [key, value] : given) {
        const std::string name = nb::cast<std::string>(key);
        if (nb::isinstance<nb::str>(value)) values.emplace_back(name, nb::cast<std::string>(value));
        else values.emplace_back(name, nb::cast<double>(value));
    }
    return values;
}

}  // namespace

NB_MODULE(_core, m) {
    m.doc() = "The mems-sketch engine.";
    m.attr("__version__") = MEMS_ENGINE_VERSION;
    // Edit sessions held by Qt objects can keep an engine until the
    // interpreter has gone; that is not a leak worth reporting at exit.
    nb::set_leak_warnings(false);
    nb::exception<mems::ExpressionError>(m, "ExpressionError", PyExc_ValueError);
    nb::exception<mems::ModelError>(m, "ModelError", PyExc_ValueError);
    auto unknown = nb::exception<mems::UnknownComponent>(m, "UnknownComponentError", PyExc_KeyError);
    nb::exception<mems::PrivateComponent>(m, "PrivateComponentError", unknown.ptr());
    auto build_error = nb::exception<mems::BuildError>(m, "BuildError", PyExc_ValueError);
    nb::exception<mems::NotSupported>(m, "NotSupported", build_error.ptr());

    nb::class_<mems::Project>(m, "Project", "A project as the engine knows it before building it.")
        .def("__init__", [](mems::Project* p, std::string_view json) { new (p) mems::Project(mems::Project::from_json(json)); },
             "json"_a)
        .def_prop_ro("name", &mems::Project::name)
        .def_prop_ro("top", &mems::Project::top)
        .def_prop_ro("scope", &mems::Project::scope, "The resolved process constants: {'process.gap': 2.0}.")
        .def("qualify", &mems::Project::qualify, "name"_a, "context"_a = nb::none(),
             "The unique name of the component ``name`` refers to, written in ``context``.")
        .def("check_references", &mems::Project::check_references)
        .def(
            "variables",
            [](const mems::Project& p, std::string_view component, const nb::dict& params) {
                return p.variables(component, values_in(params));
            },
            "component"_a, "params"_a = nb::dict(),
            "A user component's parameter values with the process constants.")
        .def(
            "points",
            [](const mems::Project& p, std::string_view component, const nb::dict& params) {
                const mems::Values values = values_in(params);
                mems::PointMap points;
                {
                    nb::gil_scoped_release release;
                    mems::Builder builder(p);
                    points = builder.build(component, values)->points;
                }
                nb::dict result;
                for (const auto& [name, point] : points) result[nb::str(name.c_str())] = nb::make_tuple(point.x, point.y);
                return result;
            },
            "component"_a, "params"_a = nb::dict(), "A component's declared points, µm.")
        .def("fingerprint", nb::overload_cast<std::string_view>(&mems::Project::fingerprint, nb::const_),
             "component"_a)
        .def(
            "build",
            [](const mems::Project& p, std::string_view component, const nb::dict& params, double grid,
               double chord) {
                const mems::Values values = values_in(params);
                mems::Layers layers;
                {
                    nb::gil_scoped_release release;  // geometry takes a while; Python may go on
                    mems::Builder builder(p);
                    layers = builder.build(component, values)->flat();
                }
                nb::dict result;
                for (const auto& [layer, region] : layers)
                    result[nb::str(layer.c_str())] = grid_polygons(region, grid, chord)[0];
                return result;
            },
            "component"_a, "params"_a = nb::dict(), "grid"_a = 0.001, "chord"_a = 0.005,
            "A component built by the engine: {layer: [(hull, [holes])]}, points in grid units, "
            "curves within chord. Raises NotSupported for what the engine does not build yet.");

    nb::class_<Engine>(m, "Engine",
                       "A project loaded for building. Builds are cached by everything they depend on, "
                       "and the cache is shared by the engines made with trial().")
        .def("__init__",
             [](Engine* e, std::string_view json) { new (e) Engine(std::make_shared<mems::BuildCache>(), json); },
             "json"_a)
        .def(
            "trial", [](const Engine& e, std::string_view json) { return Engine(e.cache, json); }, "json"_a,
            "Another version of the project, sharing this engine's cache.")
        .def_prop_ro("project", [](const Engine& e) { return *e.project; }, "The model it builds from.")
        .def(
            "build",
            [](Engine& e, std::string_view component, const nb::dict& params, double grid, double chord) {
                const mems::Values values = values_in(params);
                std::shared_ptr<const mems::Built> built;
                {
                    nb::gil_scoped_release release;  // geometry takes a while; Python may go on
                    built = e.builder->build(component, values);
                    built->flat();
                }
                return nb::make_tuple(layers_dict(built->flat(), grid, chord), points_dict(built->points));
            },
            "component"_a, "params"_a = nb::dict(), "grid"_a = 0.001, "chord"_a = 0.005,
            "(layers, points): everything flattened, {layer: ([(hull, [holes])], box)} with points and box in "
            "grid units, curves within chord; and the declared points, µm.")
        .def(
            "tree",
            [](Engine& e, std::string_view component, const nb::dict& params, double grid, double chord) {
                const mems::Values values = values_in(params);
                std::shared_ptr<const mems::Built> built;
                {
                    nb::gil_scoped_release release;
                    built = e.builder->build(component, values);
                }
                nb::dict cells;
                add_cell(*built, cells, grid, chord);
                return nb::make_tuple(nb::str(built->key.c_str()), cells, points_dict(built->points));
            },
            "component"_a, "params"_a = nb::dict(), "grid"_a = 0.001, "chord"_a = 0.005,
            "(key, cells, points): the component as cells, {key: (own layers, [(key, transform)])}, its own "
            "under ``key``; each component it places whole is a cell of its own, built and snapped once. "
            "Layers as build() gives them, transforms as (dx, dy, angle, mirror_x, scale).")
        .def(
            "output",
            [](Engine& e, std::string_view component, const nb::dict& params, double grid, double chord) {
                const mems::Values values = values_in(params);
                std::vector<std::pair<std::string, mgeom::Snapped>> snapped;
                {
                    nb::gil_scoped_release release;
                    const auto built = e.builder->build(component, values);
                    for (const auto& [layer, region] : built->flat())
                        snapped.emplace_back(layer, mgeom::snap(region, grid, chord));
                }
                nb::dict layers, reports;
                for (const auto& [layer, result] : snapped) {
                    layers[nb::str(layer.c_str())] = polygons_tuple(result);
                    reports[nb::str(layer.c_str())] = report_tuple(result.report);
                }
                return nb::make_tuple(layers, reports);
            },
            "component"_a, "params"_a = nb::dict(), "grid"_a = 0.001, "chord"_a = 0.005,
            "(layers, reports): the component as an output gets it, everything it places merged in and "
            "rounded once, from the exact geometry, onto the output's grid with curves within its chord. "
            "Layers as build() gives them; reports {layer: (exact area, snapped area, [(change, box)])} say "
            "what snapping changed in the shape of the geometry (changes: vanished, split, merged, "
            "hole_closed, hole_joined, hole_formed; boxes in µm).")
        .def(
            "records",
            [](Engine& e, std::string_view component, const nb::dict& params, double grid, double chord) {
                const mems::Values values = values_in(params);
                mems::Records records;
                std::string error;
                {
                    nb::gil_scoped_release release;
                    records = e.builder->records(component, values, &error);
                }
                nb::dict result, cells;
                for (const auto& [path, record] : records) {
                    nb::list steps;
                    for (const auto& [slot, index] : path) steps.append(nb::make_tuple(slot, index));
                    result[nb::tuple(steps)] = nb::make_tuple(
                        layers_dict(record.layers, grid, chord), instances_list(record.instances, cells, grid, chord),
                        nb::str(record.name.c_str()), points_dict(record.declared), transform_tuple(record.inner),
                        transform_tuple(record.shift));
                }
                return nb::make_tuple(result, cells, error.empty() ? nb::none() : nb::object(nb::str(error.c_str())));
            },
            "component"_a, "params"_a = nb::dict(), "grid"_a = 0.001, "chord"_a = 0.005,
            "({path: (layers, instances, name, declared points, inner, shift)}, cells, error or None): every "
            "node of the component's own shape tree as evaluated, what it places whole as instances of the "
            "cells (as tree() gives them); transforms as (dx, dy, angle, mirror_x, scale).")
        .def(
            "clear",
            [](Engine& e) {
                e.cache->clear();
                snap_cache().clear();
            },
            "Forget every cached build.")
        .def_prop_ro("cached", [](const Engine& e) { return e.cache->size(); },
                     "How many component builds the cache holds.");

    nb::class_<mems::Expression>(m, "Expression", "A parsed expression.")
        .def(nb::init<std::string_view>(), "text"_a)
        .def_prop_ro("text", &mems::Expression::text)
        .def_prop_ro("names", &mems::Expression::names,
                     "The variables it uses, dotted names whole; functions and constants excluded.")
        .def("evaluate",
             [](const mems::Expression& e, const nb::object& variables) {
                 return e.evaluate(lookup_in(variables));
             },
             "variables"_a)
        .def("__repr__", [](const mems::Expression& e) { return "Expression('" + e.text() + "')"; });

    m.def("evaluate", &evaluate, "expression"_a, "variables"_a,
          "The value of an expression (a number is its own value).");
    m.def("names_in", &mems::names_in, "expression"_a,
          "The variables an expression uses, dotted names whole.");
    m.def(
        "resolve_variables",
        [](const std::vector<std::pair<std::string, mems::Definition>>& definitions,
           const mems::Variables& fixed) { return mems::resolve_variables(definitions, fixed); },
        "definitions"_a, "fixed"_a = mems::Variables{},
        "Variables that may refer to each other, evaluated in dependency order; "
        "definitions are (name, expression or number) pairs.");
}
