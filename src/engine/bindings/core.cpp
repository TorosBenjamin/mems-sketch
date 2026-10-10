// mems_sketch._core: the mems-sketch engine for Python. So far its expressions,
// which give the same values as mems_sketch.core.expressions, and its project
// model (mems_sketch.engine.project_data makes the JSON it reads).
#include <nanobind/nanobind.h>
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

// A layer's region on a grid, as [(hull, [holes])] with points in grid units.
nb::list grid_polygons(const mgeom::Region& region, double grid, double chord) {
    nb::list polygons;
    auto ring = [](const mgeom::GridRing& points) {
        nb::list out;
        for (const auto& p : points) out.append(nb::make_tuple(p.x, p.y));
        return out;
    };
    for (const auto& polygon : mgeom::snap(region, grid, chord).polygons) {
        nb::list holes;
        for (const auto& hole : polygon.holes) holes.append(ring(hole));
        polygons.append(nb::make_tuple(ring(polygon.hull), holes));
    }
    return polygons;
}

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
                    points = builder.build(component, values).points;
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
                    layers = builder.build(component, values).layers;
                }
                nb::dict result;
                for (const auto& [layer, region] : layers)
                    result[nb::str(layer.c_str())] = grid_polygons(region, grid, chord);
                return result;
            },
            "component"_a, "params"_a = nb::dict(), "grid"_a = 0.001, "chord"_a = 0.005,
            "A component built by the engine: {layer: [(hull, [holes])]}, points in grid units, "
            "curves within chord. Raises NotSupported for what the engine does not build yet.");

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
