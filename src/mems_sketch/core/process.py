"""The fabrication process: layers and shared process constants.

Constants are available in every expression of every component as
``process.<name>`` (e.g. ``"2 * process.min_gap"``), so values that describe
the process do not have to be passed down through component parameters.
Constants may reference each other by their bare names.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mems_sketch.core.expressions import resolve_variables

Value = float | str

PROCESS_PREFIX = "process."


@dataclass
class Layer:
    """A process layer. ``undercut`` is the lateral etch loss per edge, in µm."""

    name: str
    gds_layer: int
    gds_datatype: int = 0
    undercut: float = 0.0
    min_width: float | None = None
    min_space: float | None = None


@dataclass
class Process:
    layers: dict[str, Layer] = field(default_factory=dict)
    constants: dict[str, Value] = field(default_factory=dict)

    def scope(self) -> dict[str, float]:
        """Resolved constants keyed as they appear in expressions (``process.name``)."""
        return {PROCESS_PREFIX + k: v for k, v in resolve_variables(self.constants).items()}


def default_process() -> Process:
    """Starting point for new projects: a device, anchor and metal layer."""
    return Process(
        layers={
            "device": Layer("device", 1, 0, min_width=2.0, min_space=2.0),
            "anchor": Layer("anchor", 2, 0),
            "metal": Layer("metal", 3, 0),
        }
    )
