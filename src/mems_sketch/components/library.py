"""Built-in MEMS components.

Layer names are logical ("device", "anchor", "metal"); the design maps them to
physical GDS layer/datatype pairs.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from mems_sketch.core.component import Component, Geometry, Params, register_component


@register_component
class Rectangle(Component):
    type_name = "rectangle"

    class Params(Params):
        width: float = Field(100.0, gt=0, description="Width (µm)")
        height: float = Field(50.0, gt=0, description="Height (µm)")
        layer: str = Field("device", description="Logical layer")

    def build(self, p: Params) -> Geometry:
        geo = Geometry()
        geo.add_rect(p.layer, -p.width / 2, -p.height / 2, p.width / 2, p.height / 2)
        return geo


@register_component
class Anchor(Component):
    """Device-layer pad with an anchor opening inset by ``enclosure``."""

    type_name = "anchor"

    class Params(Params):
        size: float = Field(40.0, gt=0, description="Pad edge length (µm)")
        enclosure: float = Field(3.0, ge=0, description="Device overlap of anchor (µm)")

        @model_validator(mode="after")
        def _anchor_fits(self):
            if 2 * self.enclosure >= self.size:
                raise ValueError("enclosure must be less than half the pad size")
            return self

    def build(self, p: Params) -> Geometry:
        geo = Geometry()
        h = p.size / 2
        geo.add_rect("device", -h, -h, h, h)
        inner = h - p.enclosure
        geo.add_rect("anchor", -inner, -inner, inner, inner)
        return geo


@register_component
class CombDrive(Component):
    """Interdigitated comb: a fixed comb (bottom spine) and a moving comb (top spine).

    Origin is at the centre of the finger overlap region. Alignment points:
    ``moving`` and ``fixed`` are the middle of each spine's outer edge.
    """

    type_name = "comb_drive"

    class Params(Params):
        fingers: int = Field(10, ge=1, description="Moving fingers")
        finger_width: float = Field(2.0, gt=0, description="Finger width (µm)")
        finger_length: float = Field(40.0, gt=0, description="Finger length (µm)")
        gap: float = Field(2.0, gt=0, description="Lateral finger gap (µm)")
        overlap: float = Field(20.0, ge=0, description="Initial finger overlap (µm)")
        spine_width: float = Field(10.0, gt=0, description="Spine width (µm)")

        @model_validator(mode="after")
        def _overlap_fits(self):
            if self.overlap >= self.finger_length:
                raise ValueError("overlap must be shorter than the finger length")
            return self

    def build(self, p: Params) -> Geometry:
        geo = Geometry()
        pitch = 2 * (p.finger_width + p.gap)
        # Fixed comb has one more finger so every moving finger sits between two.
        n_fixed = p.fingers + 1
        total = n_fixed * p.finger_width + p.fingers * p.finger_width + 2 * p.fingers * p.gap
        x_start = -total / 2

        fixed_top = p.overlap / 2
        fixed_base = fixed_top - p.finger_length
        moving_bottom = -p.overlap / 2
        moving_base = moving_bottom + p.finger_length

        for i in range(n_fixed):
            x = x_start + i * pitch
            geo.add_rect("device", x, fixed_base, x + p.finger_width, fixed_top)
        for i in range(p.fingers):
            x = x_start + p.finger_width + p.gap + i * pitch
            geo.add_rect("device", x, moving_bottom, x + p.finger_width, moving_base)

        geo.add_rect("device", x_start, fixed_base - p.spine_width, x_start + total, fixed_base)
        geo.add_rect("device", x_start, moving_base, x_start + total, moving_base + p.spine_width)
        return geo

    def points(self, p: Params) -> dict[str, tuple[float, float]]:
        moving_edge = -p.overlap / 2 + p.finger_length + p.spine_width
        fixed_edge = p.overlap / 2 - p.finger_length - p.spine_width
        return {"moving": (0.0, moving_edge), "fixed": (0.0, fixed_edge)}


@register_component
class SerpentineSpring(Component):
    """Meandering beam spring running along +y from the origin.

    Alignment points: ``start`` and ``end`` are the middle of the outer edge of
    the first and the last beam.
    """

    type_name = "serpentine_spring"

    class Params(Params):
        turns: int = Field(3, ge=1, description="Number of meanders")
        beam_width: float = Field(3.0, gt=0, description="Beam width (µm)")
        span: float = Field(80.0, gt=0, description="Meander span, x (µm)")
        pitch: float = Field(15.0, gt=0, description="Meander pitch, y (µm)")

        @model_validator(mode="after")
        def _beams_do_not_touch(self):
            if self.pitch <= self.beam_width:
                raise ValueError("pitch must exceed beam width")
            return self

    def build(self, p: Params) -> Geometry:
        geo = Geometry()
        w, h = p.beam_width, p.span / 2
        rows = 2 * p.turns + 1
        for i in range(rows):
            y = i * p.pitch
            geo.add_rect("device", -h, y, h, y + w)  # long beam
            if i < rows - 1:
                x = h - w if i % 2 == 0 else -h
                geo.add_rect("device", x, y, x + w, y + p.pitch + w)  # connector
        return geo

    def points(self, p: Params) -> dict[str, tuple[float, float]]:
        return {"start": (0.0, 0.0), "end": (0.0, 2 * p.turns * p.pitch + p.beam_width)}
