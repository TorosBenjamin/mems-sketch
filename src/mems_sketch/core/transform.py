"""Placements in the plane: a plain value, independent of any geometry library.

A :class:`Transform` mirrors about the x axis (if ``mirror``), rotates
counter-clockwise by ``angle`` degrees, scales by ``mag`` and then moves by
``(dx, dy)`` µm, in that order. Shape placements, alignment moves and the
frames of nested nodes are all transforms; the geometry backend turns them
into its own type where it applies them to geometry.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Rotations that are a whole multiple of 90° are exact: no rounding in sin/cos.
_QUARTER = {0: (0.0, 1.0), 1: (1.0, 0.0), 2: (0.0, -1.0), 3: (-1.0, 0.0)}


def _sin_cos(angle: float) -> tuple[float, float]:
    quarters = angle / 90
    if quarters == int(quarters):
        return _QUARTER[int(quarters) % 4]
    radians = math.radians(angle)
    return math.sin(radians), math.cos(radians)


def _normal_angle(angle: float) -> float:
    angle = math.fmod(angle, 360.0)
    if angle < 0:
        angle += 360.0
    return 0.0 if angle >= 360.0 - 1e-12 else angle


@dataclass(frozen=True)
class Transform:
    dx: float = 0.0
    dy: float = 0.0
    angle: float = 0.0  # degrees, counter-clockwise, in [0, 360)
    mirror: bool = False  # about the x axis, before the rotation
    mag: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "angle", _normal_angle(self.angle))

    @classmethod
    def moving(cls, dx: float, dy: float) -> Transform:
        return cls(dx, dy)

    @classmethod
    def rotating(cls, angle: float, about: tuple[float, float] = (0.0, 0.0)) -> Transform:
        """A rotation by ``angle`` degrees about the point ``about``."""
        px, py = about
        return cls(px, py) * cls(angle=angle) * cls(-px, -py)

    @classmethod
    def reflecting(cls, angle: float, through: tuple[float, float] = (0.0, 0.0)) -> Transform:
        """The mirror image across the line through ``through`` at ``angle`` degrees."""
        px, py = through
        return cls(px, py) * cls(angle=2 * angle, mirror=True) * cls(-px, -py)

    @property
    def is_identity(self) -> bool:
        return self == Transform()

    def apply_vector(self, x: float, y: float) -> tuple[float, float]:
        """A displacement transformed: rotated, mirrored and scaled, not moved."""
        if self.mirror:
            y = -y
        s, c = _sin_cos(self.angle)
        return self.mag * (c * x - s * y), self.mag * (s * x + c * y)

    def apply(self, x: float, y: float) -> tuple[float, float]:
        vx, vy = self.apply_vector(x, y)
        return vx + self.dx, vy + self.dy

    def __mul__(self, other: Transform) -> Transform:
        """``self * other`` applies ``other`` first."""
        if not isinstance(other, Transform):
            return NotImplemented
        # A mirror turns the rotation that follows it the other way: M R(a) = R(-a) M.
        angle = self.angle + (-other.angle if self.mirror else other.angle)
        dx, dy = self.apply(other.dx, other.dy)
        return Transform(dx, dy, angle, self.mirror != other.mirror, self.mag * other.mag)

    def inverted(self) -> Transform:
        angle = self.angle if self.mirror else -self.angle
        linear = Transform(angle=angle, mirror=self.mirror, mag=1 / self.mag)
        dx, dy = linear.apply_vector(-self.dx, -self.dy)
        return Transform(dx, dy, angle, self.mirror, 1 / self.mag)

    def __repr__(self) -> str:
        parts = [f"dx={self.dx:g}", f"dy={self.dy:g}"]
        if self.angle:
            parts.append(f"angle={self.angle:g}")
        if self.mirror:
            parts.append("mirror=True")
        if self.mag != 1:
            parts.append(f"mag={self.mag:g}")
        return f"Transform({', '.join(parts)})"


IDENTITY = Transform()
