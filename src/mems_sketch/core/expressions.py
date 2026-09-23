"""Safe evaluation of parameter expressions such as ``"2 * w_beam + gap"``.

Only arithmetic, numeric literals, variable names and a small set of math
functions are allowed; anything else raises :class:`ExpressionError`.
"""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable, Mapping

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
    """Variable names referenced by ``expression`` (functions and constants excluded).

    Dotted names such as ``process.min_gap`` are returned whole.
    """
    names: set[str] = set()

    def visit(node: ast.AST) -> None:
        dotted = _dotted_name(node)
        if dotted is not None:
            if dotted not in _FUNCTIONS and dotted not in _CONSTANTS:
                names.add(dotted)
            return
        if isinstance(node, ast.Call):  # the function name itself is not a variable
            for arg in node.args:
                visit(arg)
            return
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(_parse(expression))
    return names


def evaluate(expression: str | float, variables: Mapping[str, float]) -> float:
    if isinstance(expression, (int, float)):
        return float(expression)
    return float(_eval(_parse(expression).body, variables))


def resolve_variables(
    expressions: Mapping[str, str | float], fixed: Mapping[str, float] | None = None
) -> dict[str, float]:
    """Evaluate variables that may reference each other, in dependency order.

    ``fixed`` holds values that are already known (e.g. process constants); they
    can be referenced but are not part of the result.
    """
    fixed = fixed or {}
    resolved: dict[str, float] = {}
    visiting: set[str] = set()

    def visit(name: str) -> float:
        if name in resolved:
            return resolved[name]
        if name not in expressions and name in fixed:
            return fixed[name]
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


def substitute(expression: str, change: Callable[[str], str | None]) -> str:
    """``expression`` with each (dotted) name replaced by ``change(name)``, if not None.

    Replacements are inserted as sub-expressions, so ``w`` -> ``a + b`` turns
    ``2 * w`` into ``2 * (a + b)``. An expression without replacements is
    returned unchanged, keeping its formatting.
    """
    tree = _parse(expression)
    changed = False

    class Replace(ast.NodeTransformer):
        def _name(self, node: ast.AST) -> ast.AST:
            nonlocal changed
            dotted = _dotted_name(node)
            if dotted is None:
                return self.generic_visit(node)
            if dotted in _FUNCTIONS or dotted in _CONSTANTS:
                return node
            replacement = change(dotted)
            if replacement is None:
                return node
            changed = True
            return _parse(str(replacement)).body

        visit_Name = _name
        visit_Attribute = _name

        def visit_Call(self, node: ast.Call) -> ast.AST:
            node.args = [self.visit(arg) for arg in node.args]
            return node

    result = Replace().visit(tree)
    return ast.unparse(result) if changed else expression


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
        case ast.Name() | ast.Attribute():
            name = _dotted_name(node)
            if name is None:
                raise ExpressionError(f"unsupported syntax: {ast.dump(node)}")
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


def _dotted_name(node: ast.AST) -> str | None:
    """``a`` for a name, ``a.b.c`` for an attribute chain on a name, else None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))
