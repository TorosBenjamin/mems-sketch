"""Layers relative to a component's level of the layer stack (requirement CMP-10).

Every component is on a level of the process's layer stack. A placement can
set it; else it is the component's own default level, if it declares one;
else the level of the component placing it. Inside, a shape's ``layer`` is:

- ``level``: the component's level (its main layer);
- ``level+1``, ``level-2``: that many levels above or below;
- ``level.anchor``, ``level-1.anchor``: a role of that level;
- anything else: a layer by name (``metal``).

A placement's ``level`` is ``level``, ``level+1``... (relative to the placing
component's level) or a level by name (``poly2``). Counting levels, not
layers, means adding a layer to a level never moves anything.

A process that declares no layer stack has the default one
(:data:`~mems_sketch.core.process.DEFAULT_LEVELS`: device, with anchor as its
anchor layer, then metal), so components drawn relative to their level work
in any project.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from mems_sketch.core.process import DEFAULT_LEVELS, Level

LEVEL = "level"
_RELATIVE = re.compile(r"level(?:([+-])(\d+))?(?:\.([A-Za-z_]\w*))?")


def is_relative(spec: str) -> bool:
    """Whether a layer or level is written relative to the component's level."""
    return _RELATIVE.fullmatch(spec) is not None


@dataclass(frozen=True)
class Stack:
    """The process's layer stack, bottom to top, and its default level."""

    levels: tuple[Level, ...] = ()
    default: str | None = None

    @classmethod
    def of(cls, levels: Sequence[Level], default: str | None = None) -> Stack:
        """The stack of a process; the default one when it declares none."""
        return cls(tuple(levels), default) if levels else cls(DEFAULT_LEVELS)

    def key(self) -> str:
        """A text that changes whenever the stack does (for cache keys)."""
        return repr([(lv.layer, sorted(lv.roles.items())) for lv in self.levels]) + repr(
            self.default
        )

    def top_level(self, own: str | None) -> str | None:
        """The level of a component built on its own: its default, else the stack's."""
        if own is not None:
            return self._checked(own)
        if self.default is not None:
            return self.default
        return self.levels[0].layer if self.levels else None

    def place(self, spec: str | None, own: str | None, current: str | None) -> str | None:
        """The level of a placed component: the placement's ``spec``, else the
        component's own default level, else the placing component's level."""
        if spec is None:
            return current if own is None else self._checked(own)
        match = _RELATIVE.fullmatch(spec)
        if match is None:
            return self._checked(spec)
        if match.group(3):
            raise ValueError(f"a component is placed on a level, not on a role: '{spec}'")
        return self.levels[self._offset(spec, match, current)].layer

    def layer(self, spec: str, current: str | None) -> str:
        """The layer a shape's ``layer`` names, on the level ``current``."""
        match = _RELATIVE.fullmatch(spec)
        if match is None:
            return spec
        level = self.levels[self._offset(spec, match, current)]
        role = match.group(3)
        if role is None:
            return level.layer
        if role not in level.roles:
            raise ValueError(f"level '{level.layer}' has no '{role}' layer (for '{spec}')")
        return level.roles[role]

    def _offset(self, spec: str, match: re.Match[str], current: str | None) -> int:
        if current is None:
            raise ValueError(f"'{spec}' needs the component to be on a level of the layer stack")
        index = self._index(current)
        sign, count = match.group(1), int(match.group(2) or 0)
        target = index + (count if sign == "+" else -count)
        if target < 0:
            raise ValueError(f"'{spec}' on '{current}' is below the bottom of the layer stack")
        if target >= len(self.levels):
            raise ValueError(f"'{spec}' on '{current}' is above the top of the layer stack")
        return target

    def _index(self, level: str) -> int:
        for index, candidate in enumerate(self.levels):
            if candidate.layer == level:
                return index
        raise ValueError(f"'{level}' is not a level of the layer stack")

    def _checked(self, level: str) -> str:
        self._index(level)
        return level


DEFAULT_STACK = Stack(DEFAULT_LEVELS)
