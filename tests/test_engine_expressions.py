"""The engine's expressions (C++, mems_sketch._core) against the Python ones they
replace (mems_sketch.core.expressions): the same values to the last bit, the
same names, and an error where Python has one (core-architecture.md, step 5).

Skipped when the engine is not built, unless MGEOM_REQUIRED is set (CI)."""

import math
import os
import random
from pathlib import Path

import pytest
import yaml

from mems_sketch.core import expressions as py


def _engine():
    try:
        from mems_sketch import _core
    except ImportError:
        try:
            import _core  # built in place: PYTHONPATH=build/engine
        except ImportError:
            if os.environ.get("MGEOM_REQUIRED"):
                raise
            pytest.skip("the engine (mems_sketch._core) is not built", allow_module_level=True)
    return _core


core = _engine()

VARIABLES = {
    "a": 3,
    "b": 2.5,
    "c": 0.0,
    "d": -7,
    "e": 1e308,
    "f": 0,
    "n": 2,
    "big": 2**52,
    "process.gap": 1.5,
    "beam.right.x": -0.25,
}


def outcome(evaluate, text, variables=VARIABLES):
    try:
        return ("value", evaluate(text, variables))
    except Exception:  # noqa: BLE001 - any error: both must fail
        return ("error", None)


def same(a, b) -> bool:
    if a[0] != b[0]:
        return False
    if a[0] == "error":
        return True
    x, y = a[1], b[1]
    if math.isnan(x) or math.isnan(y):
        return math.isnan(x) and math.isnan(y)
    return x == y and math.copysign(1, x) == math.copysign(1, y)


def check(text):
    python, engine = outcome(py.evaluate, text), outcome(core.evaluate, text)
    assert same(python, engine), f"{text!r}: Python {python}, engine {engine}"
    if python[0] == "value":
        assert core.names_in(text) == py.names_in(text), text


EDGE_CASES = [
    "2 * a + b",
    "-2 ** 2",
    "2 ** -1",
    "2 ** 3 ** 2",
    "(-2) ** 3",
    "(-8) ** (1 / 3)",
    "0 ** 0",
    "0.0 ** -1",
    "10.0 ** 400",
    "10 ** 400",
    "big * big * big * big * big * big",
    "3 ** 40",
    "7 // 2",
    "-7 // 2",
    "7 // -2.0",
    "-7 % 3",
    "7 % -3",
    "5.5 % -2",
    "-1 % e",
    "1 % 0",
    "1 // 0.0",
    "1 / 0",
    "1 / c",
    "e * 10",
    "-e * 10 - e * 10",
    "round(2.5) + round(3.5) + round(-2.5)",
    "round(2.675, 2)",
    "round(0.125, 2)",
    "round(-0.4)",
    "round(-0.4, 0)",
    "round(1250.4, -2)",
    "round(1250, -2)",
    "round(1350, -2)",
    "round(-1250, -2)",
    "round(a, n)",
    "round(b, n)",
    "round(b, b)",
    "round(e, -308)",
    "round(e, -309)",
    "round(1.5, 400)",
    "round(5e-324, 400)",
    "ceil(b) + floor(-b) + abs(d)",
    "ceil(e * 10)",
    "sqrt(-1)",
    "sqrt(-c)",
    "sqrt(a)",
    "sin(e * 10)",
    "sin(1) + cos(2) + tan(3)",
    "min(3)",
    "min()",
    "max(1, 2, 3, 2.5)",
    "min(c, -c)",
    "max(b, a, 3.0)",
    "abs()",
    "pi",
    "pi * 2 ** 0.5",
    "process.gap + beam.right.x",
    "process.gap.x",
    "(a).real",
    "a.real",
    "foo(a)",
    "a(1)",
    "sqrt",
    "1_000 + 0x1F + 0o17 + 0b101",
    "1e3 + .5 + 2.",
    "1_0.5e1_0",
    "012",
    "00",
    "0_0",
    "012.5",
    "1j",
    "2x",
    "1__0",
    "1e",
    "--a + +-b",
    "not a",
    "a < b",
    "a if b else d",
    "a and b",
    "[a]",
    "(a, b)",
    "max(a, b,)",
    "max(a=1)",
    "'s'",
    "True",
    "None",
    "a.if",
    "a @ b",
    "a << 1",
    "~a",
    "",
    "   a   ",
    "(a\n + b)",
    "a\n + b",
    "a # comment",
    "lambda: 1",
    "min(c, nan)",
    "1e400 - 1e400",
    "1e400 * 0",
]


@pytest.mark.parametrize("text", EDGE_CASES)
def test_edge_cases(text):
    check(text)


def project_expressions():
    """Every string value in the example projects that Python can parse."""
    found = set()
    root = Path(__file__).parent.parent / "examples"

    def walk(value):
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str):
            try:
                py.names_in(value)
            except Exception:  # noqa: BLE001 - not an expression (a name, a description)
                return
            found.add(value)

    for path in root.rglob("*.yaml"):
        walk(yaml.safe_load(path.read_text()))
    return sorted(found)


def test_the_examples_expressions():
    texts = project_expressions()
    assert len(texts) > 20
    for text in texts:
        names = py.names_in(text)
        variables = {name: 1.0 + i / 7 for i, name in enumerate(sorted(names))}
        python = outcome(py.evaluate, text, variables)
        engine = outcome(core.evaluate, text, variables)
        assert same(python, engine), f"{text!r}: Python {python}, engine {engine}"
        assert core.names_in(text) == names


NUMBERS = [
    "0",
    "1",
    "2",
    "3",
    "7",
    "10",
    "0.5",
    "2.5",
    "2.675",
    "0.1",
    "1e-3",
    "1e300",
    "0.0",
    "12",
]
NAMES = [*VARIABLES, "pi", "unknown"]
FUNCTIONS = ["abs", "min", "max", "round", "sqrt", "sin", "cos", "tan", "ceil", "floor"]
OPERATORS = ["+", "-", "*", "/", "//", "%", "**"]
EXPONENTS = [
    "0",
    "1",
    "2",
    "3",
    "-1",
    "-2",
    "0.5",
    "2.5",
    "a",
    "b",
    "c",
    "d",
    "f",
    "n",
    "(-1)",
    "(1 / 3)",
]


def generate(rng: random.Random, depth: int = 0) -> str:
    roll = rng.random()
    if depth > 3 or roll < 0.3:
        return rng.choice(NUMBERS) if rng.random() < 0.5 else rng.choice(NAMES)
    if roll < 0.6:
        op = rng.choice(OPERATORS)
        # Python's integers are exact: a large integer power would take forever.
        right = rng.choice(EXPONENTS) if op == "**" else generate(rng, depth + 1)
        return f"{generate(rng, depth + 1)} {op} {right}"
    if roll < 0.7:
        return f"{rng.choice('+-')}{generate(rng, depth + 1)}"
    if roll < 0.8:
        return f"({generate(rng, depth + 1)})"
    function = rng.choice(FUNCTIONS)
    if function == "round" and rng.random() < 0.6:  # digits stay small: see EXPONENTS
        digits = rng.choice(["0", "1", "2", "-1", "-2", "n", "a", "b", "f"])
        return f"round({generate(rng, depth + 1)}, {digits})"
    if function in ("min", "max"):
        count = rng.choice([1, 1, 1, 2, 3])
    else:  # round's second argument comes from the list above
        count = 1 if function == "round" else rng.choice([1, 1, 1, 1, 2])
    return f"{function}({', '.join(generate(rng, depth + 1) for _ in range(count))})"


def test_generated_expressions():
    rng = random.Random(20261009)
    for _ in range(20_000):
        check(generate(rng))


def test_variables_that_refer_to_each_other():
    definitions = {"length": "2 * w + process.gap", "w": "3", "h": 4.0, "area": "length * h"}
    fixed = {"process.gap": 1.5}
    assert core.resolve_variables(list(definitions.items()), fixed) == py.resolve_variables(
        definitions, fixed
    )
    for broken in ({"a": "b", "b": "a + 1"}, {"a": "missing"}, {"a": "1 +"}):
        with pytest.raises(ValueError):
            py.resolve_variables(broken)
        with pytest.raises(core.ExpressionError):
            core.resolve_variables(list(broken.items()))


def test_parsed_once_evaluated_often():
    expression = core.Expression("2 * w + process.gap")
    assert expression.names == {"w", "process.gap"}
    assert [expression.evaluate({"w": w, "process.gap": 1}) for w in (1, 2)] == [3.0, 5.0]
    assert issubclass(core.ExpressionError, ValueError)
