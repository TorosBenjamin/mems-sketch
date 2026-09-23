"""Names for new things: shapes, points, parameters, constants, layers."""

import re


def fresh_name(name: str, taken: set[str]) -> str:
    """``name`` with its numeric suffix replaced by the lowest one not in ``taken``."""
    stem = re.sub(r"\d+$", "", name) or "shape"
    n = 1
    while f"{stem}{n}" in taken:
        n += 1
    return f"{stem}{n}"
