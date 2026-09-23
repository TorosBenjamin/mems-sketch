"""Safe evaluation of parameter expressions such as ``"2 * w_beam + gap"``.

Only arithmetic, numeric literals, variable names and a small set of math
functions are allowed; anything else raises :class:`ExpressionError`.
"""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Mapping

_BINARY_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "ceil": math.ceil,
    "floor": math.floor,
}
_CONSTANTS = {"pi": math.pi}
RESERVED_NAMES = frozenset(_FUNCTIONS) | frozenset(_CONSTANTS)


class ExpressionError(ValueError):
    pass


def names_in(expression: str) -> set[str]:
    """Variable names referenced by ``expression`` (functions and constants excluded)."""
    tree = _parse(expression)
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id not in _FUNCTIONS and node.id not in _CONSTANTS
    }


def evaluate(expression: str | float | int, variables: Mapping[str, float]) -> float:
    if isinstance(expression, (int, float)):
        return float(expression)
    return float(_eval(_parse(expression).body, variables))


def resolve_variables(expressions: Mapping[str, str | float]) -> dict[str, float]:
    """Evaluate global variables that may reference each other, in dependency order."""
    resolved: dict[str, float] = {}
    visiting: set[str] = set()

    def visit(name: str) -> float:
        if name in resolved:
            return resolved[name]
        if name in visiting:
            raise ExpressionError(f"circular reference involving '{name}'")
        if name not in expressions:
            raise ExpressionError(f"unknown variable '{name}'")
        visiting.add(name)
        expr = expressions[name]
        deps = names_in(expr) if isinstance(expr, str) else set()
        resolved[name] = evaluate(expr, {dep: visit(dep) for dep in deps})
        visiting.discard(name)
        return resolved[name]

    for name in expressions:
        visit(name)
    return resolved


def _parse(expression: str) -> ast.Expression:
    try:
        return ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"invalid expression '{expression}': {exc.msg}") from None


def _eval(node: ast.AST, variables: Mapping[str, float]) -> float:
    match node:
        case ast.Constant(value=value) if isinstance(value, (int, float)) and not isinstance(
            value, bool
        ):
            return value
        case ast.Name(id=name):
            if name in variables:
                return variables[name]
            if name in _CONSTANTS:
                return _CONSTANTS[name]
            raise ExpressionError(f"unknown variable '{name}'")
        case ast.BinOp(left=left, op=op, right=right) if type(op) in _BINARY_OPS:
            return _BINARY_OPS[type(op)](_eval(left, variables), _eval(right, variables))
        case ast.UnaryOp(op=op, operand=operand) if type(op) in _UNARY_OPS:
            return _UNARY_OPS[type(op)](_eval(operand, variables))
        case ast.Call(func=ast.Name(id=fname), args=args, keywords=[]) if fname in _FUNCTIONS:
            return _FUNCTIONS[fname](*(_eval(arg, variables) for arg in args))
    raise ExpressionError(f"unsupported syntax: {ast.dump(node)}")
