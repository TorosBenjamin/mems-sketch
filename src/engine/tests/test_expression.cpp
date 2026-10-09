// Expressions follow Python's grammar and arithmetic (the Python equivalence
// test, tests/test_engine_expressions.py, compares them on thousands more).
#include <doctest/doctest.h>

#include <cmath>

#include "mems/expression.hpp"

using mems::evaluate;
using mems::Expression;
using mems::ExpressionError;
using mems::Variables;

namespace {
double ev(const char* text, const Variables& v = {}) { return evaluate(text, v); }
}  // namespace

TEST_CASE("arithmetic and precedence as in Python") {
    CHECK(ev("2 * w + gap", {{"w", 3}, {"gap", 1.5}}) == 7.5);
    CHECK(ev("-2 ** 2") == -4);
    CHECK(ev("2 ** -1") == 0.5);
    CHECK(ev("2 ** 3 ** 2") == 512);
    CHECK(ev("(1 + 2) * 3") == 9);
    CHECK(ev("7 // 2") == 3);
    CHECK(ev("-7 // 2") == -4);
    CHECK(ev("-7 % 3") == 2);
    CHECK(ev("7 % -3") == -2);
    CHECK(ev("-7.5 // 2") == -4);
    CHECK(ev("5.5 % -2") == -0.5);
    CHECK(ev("1_000 + 0x10 + 0o7 + 0b11") == 1026);
    CHECK(ev("1e3 + .5 + 2.") == 1002.5);
    CHECK(ev("--1 + +2") == 3);
    CHECK(ev("1 # a comment") == 1);
}

TEST_CASE("functions and constants") {
    CHECK(ev("max(1, 5, 3) + min(4, 2)") == 7);
    CHECK(ev("round(2.5) + round(3.5)") == 6);  // half to even
    CHECK(ev("round(2.675, 2)") == 2.67);        // 2.675 is a little below it
    CHECK(ev("round(1250.4, -2)") == 1300);
    CHECK(ev("round(1250, -2)") == 1200);
    CHECK(ev("ceil(1.2) + floor(-1.2) + abs(-3)") == 2 + -2 + 3);
    CHECK(ev("sqrt(16)") == 4);
    CHECK(ev("pi") == doctest::Approx(3.14159265));
    CHECK(ev("pi", {{"pi", 3}}) == 3);  // a variable comes before a constant
}

TEST_CASE("dotted names") {
    Expression e("process.min_gap + beam.right.x - sqrt(w)");
    CHECK(e.names() == std::set<std::string>{"beam.right.x", "process.min_gap", "w"});
    CHECK(e.evaluate(Variables{{"process.min_gap", 2}, {"beam.right.x", 10}, {"w", 4}}) == 10);
    CHECK(mems::names_in("foo(a) + pi") == std::set<std::string>{"a"});
}

TEST_CASE("errors") {
    CHECK_THROWS_WITH_AS(ev("w + 1"), "unknown variable 'w'", ExpressionError);
    CHECK_THROWS_AS(ev("1 / 0"), ExpressionError);
    CHECK_THROWS_AS(ev("1 // 0.0"), ExpressionError);
    CHECK_THROWS_AS(ev("0 ** -1"), ExpressionError);
    CHECK_THROWS_AS(ev("(-8) ** (1 / 3)"), ExpressionError);
    CHECK_THROWS_AS(ev("sqrt(-1)"), ExpressionError);
    CHECK_THROWS_AS(ev("round(1.5, 1.0)"), ExpressionError);  // digits must be an integer
    CHECK_THROWS_AS(ev("min(3)"), ExpressionError);
    CHECK_THROWS_AS(ev("10.0 ** 400"), ExpressionError);
    for (const char* bad : {"1 +", "(1", "1)", "a < b", "a if b else c", "'x'", "1j", "012", "2x",
                            "f(1)", "True", "a.if", "[1]", "1, 2", "x = 1", "lambda: 1"}) {
        CAPTURE(bad);
        CHECK_THROWS_AS(ev(bad, {{"a", 1}, {"b", 1}, {"x", 1}}), ExpressionError);
    }
}

TEST_CASE("variables that refer to each other") {
    auto values = mems::resolve_variables(
        {{"length", std::string("2 * w + process.gap")}, {"w", std::string("3")}, {"h", 4.0}},
        {{"process.gap", 1}});
    CHECK(values == std::map<std::string, double>{{"h", 4}, {"length", 7}, {"w", 3}});
    CHECK_THROWS_WITH_AS(mems::resolve_variables({{"a", std::string("b")}, {"b", std::string("a + 1")}}),
                         "circular reference involving 'a'", ExpressionError);
}
