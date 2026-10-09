"""Declared settings of plugins (exporters, rule kinds): from the declaration
come the forms in the GUI, the command line's flags and the checking of
values."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Option:
    """One setting a plugin takes: an exporter's options, a rule kind's
    parameters. Its type is the type of ``default``: ``bool``, ``int``,
    ``float`` or ``str`` (with ``choices``, one of them)."""

    name: str
    default: bool | int | float | str
    label: str
    help: str = ""
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = field(default_factory=tuple)
    suffix: str = ""  # a unit, shown after the value
    decimals: int = 3  # for floats in forms

    @property
    def value_type(self) -> type:
        return type(self.default)

    def parse(self, text: str) -> bool | int | float | str:
        """A value typed on the command line."""
        if self.value_type is bool:
            lowered = text.strip().lower()
            if lowered in ("1", "true", "yes", "on"):
                return self.check(True)
            if lowered in ("0", "false", "no", "off"):
                return self.check(False)
            raise ValueError(f"option '{self.name}' is yes or no, not '{text}'")
        try:
            value = self.value_type(text)
        except ValueError:
            raise ValueError(
                f"option '{self.name}' is a{'n' if self.value_type is int else ''} "
                f"{self.value_type.__name__}, not '{text}'"
            ) from None
        return self.check(value)

    def check(self, value: Any) -> bool | int | float | str:
        """``value`` as this option's type, or ValueError."""
        kind = self.value_type
        if kind is bool:
            if not isinstance(value, bool):
                raise ValueError(f"option '{self.name}' is yes or no, not {value!r}")
        elif kind in (int, float):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"option '{self.name}' is a number, not {value!r}")
            if kind is int and value != int(value):
                raise ValueError(f"option '{self.name}' is a whole number, not {value!r}")
            value = kind(value)
            if not math.isfinite(value):
                raise ValueError(f"option '{self.name}' must be finite")
            if self.minimum is not None and value < self.minimum:
                raise ValueError(f"option '{self.name}' is at least {self.minimum:g}")
            if self.maximum is not None and value > self.maximum:
                raise ValueError(f"option '{self.name}' is at most {self.maximum:g}")
        else:
            if not isinstance(value, str):
                raise ValueError(f"option '{self.name}' is text, not {value!r}")
            if self.choices and value not in self.choices:
                raise ValueError(
                    f"option '{self.name}' is one of {', '.join(self.choices)}, not '{value}'"
                )
        return value
