"""Errors in words: what every frontend (the editor, the command line, the MCP
server) shows when something is refused, without a validation library's
wording ("1 validation error for ...", "[type=...]", links to its site)."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError


def explain(error: BaseException) -> str:
    """``error`` as a message for the user: a validation error one problem per
    field (``points: needs at least 3 items, not 2``), a missing key by its
    text, anything else as it says."""
    if isinstance(error, ValidationError):
        return "; ".join(_problem(p) for p in error.errors())
    if isinstance(error, KeyError) and error.args:
        return str(error.args[0])
    return str(error) or type(error).__name__


def _problem(problem: dict[str, Any]) -> str:
    where = ".".join(
        str(part) for part in problem["loc"] if not str(part).startswith(("function-", "tagged-"))
    )
    kind, limits, given = problem["type"], problem.get("ctx") or {}, problem.get("input")
    if kind == "value_error":
        text = str(problem["msg"]).removeprefix("Value error, ")
        return f"{where}: {text}" if where and not text.startswith(f"'{where}'") else text
    if kind == "missing":
        text = "is required"
    elif kind == "extra_forbidden":
        text = "is not a field here"
    elif kind == "too_short":
        text = f"needs at least {limits.get('min_length')} items, not {limits.get('actual_length')}"
    elif kind == "too_long":
        text = f"takes at most {limits.get('max_length')} items, not {limits.get('actual_length')}"
    elif kind == "literal_error":
        text = f"is one of {limits.get('expected')}, not {given!r}"
    elif kind in ("greater_than_equal", "greater_than", "less_than_equal", "less_than"):
        words = {
            "greater_than_equal": ("at least", "ge"),
            "greater_than": ("more than", "gt"),
            "less_than_equal": ("at most", "le"),
            "less_than": ("less than", "lt"),
        }[kind]
        text = f"must be {words[0]} {limits.get(words[1])}, not {given}"
    elif kind in ("int_from_float", "int_parsing", "int_type"):
        text = f"must be a whole number, not {given!r}"
    else:
        text = str(problem["msg"])
    return f"{where}: {text}" if where else text
