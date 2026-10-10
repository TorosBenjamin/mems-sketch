// mems_sketch._core: the mems-sketch engine for Python. So far its expressions,
// which give the same values as mems_sketch.core.expressions.
#include <nanobind/nanobind.h>
#include <nanobind/stl/map.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/set.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/string_view.h>
#include <nanobind/stl/variant.h>
#include <nanobind/stl/vector.h>

#include "mems/expression.hpp"

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

}  // namespace

NB_MODULE(_core, m) {
    m.doc() = "The mems-sketch engine.";
    m.attr("__version__") = MEMS_ENGINE_VERSION;
    nb::exception<mems::ExpressionError>(m, "ExpressionError", PyExc_ValueError);

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
