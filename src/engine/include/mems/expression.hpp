// Parameter expressions such as "2 * w_beam + gap": the same language as
// mems_sketch.core.expressions, evaluated to the same values.
//
// Numbers, variable names (dotted ones such as "process.min_gap" are one name),
// + - * / // % **, unary + and -, parentheses, the functions abs, min, max,
// round, sqrt, sin, cos, tan, ceil and floor, and the constant pi. Arithmetic
// follows Python's: // and % round towards minus infinity, and integers stay
// integers until they meet a float or "/" (which matters for round's second
// argument). Anything else is an ExpressionError.
#pragma once

#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <variant>
#include <vector>

namespace mems {

class ExpressionError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

// A value as expressions see it: Python's int or float. Integers are exact up
// to 2^53 (Python's are unbounded; designs never come near).
struct Number {
    double value = 0.0;
    bool integer = false;

    static Number of_int(double v) { return {v + 0.0, true}; }  // + 0.0: no -0
    static Number of_float(double v) { return {v, false}; }
};

// What a variable is worth, or nothing when it is not defined.
using Lookup = std::function<std::optional<Number>(std::string_view name)>;
using Variables = std::map<std::string, double, std::less<>>;

class Expression {
public:
    // Parses ``text``; throws ExpressionError when it is not an expression.
    explicit Expression(std::string_view text);

    const std::string& text() const { return text_; }

    // The variables it uses, dotted names whole; functions and constants excluded.
    const std::set<std::string>& names() const { return names_; }

    // Its value; throws ExpressionError (unknown variable, division by zero, ...).
    double evaluate(const Variables& variables) const;
    double evaluate(const Lookup& lookup) const;
    Number evaluate_number(const Lookup& lookup) const;

    struct Node;

private:
    std::string text_;
    std::shared_ptr<const Node> root_;
    std::set<std::string> names_;
};

double evaluate(std::string_view text, const Variables& variables);
std::set<std::string> names_in(std::string_view text);

// A variable's definition: an expression or a number.
using Definition = std::variant<std::string, double>;

// Variables that may refer to each other, evaluated in dependency order.
// ``fixed`` are known values (process constants) they can use; they are not
// part of the result. Throws on a circular or unknown reference.
// Definitions are visited in the order given (which decides which problem is
// reported first).
std::map<std::string, double> resolve_variables(
    const std::vector<std::pair<std::string, Definition>>& definitions,
    const Variables& fixed = {});

}  // namespace mems
