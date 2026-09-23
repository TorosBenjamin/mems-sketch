"""Builds a comb-driven resonator and saves it as resonator.mems for the GUI.

Run with:  python examples/resonator.py && mems-sketch resonator.mems
"""

from mems_sketch import BooleanShape, CircleShape, Instance, RectShape, Repeat, save
from mems_sketch.gui.document import new_design

design = new_design()
design.name = "resonator"
design.layers["device"].undercut = 0.3
for name, value in {"plate": 160, "pitch": 20, "hole_r": 3, "w_finger": 2}.items():
    design.set_variable(name, value)

# Proof mass: a plate with a parametric grid of release holes.
design.add(
    BooleanShape(
        name="proof_mass",
        op="subtract",
        a=[
            RectShape(
                name="plate_outline",
                layer="device",
                x0="-plate/2",
                y0="-plate/2",
                x1="plate/2",
                y1="plate/2",
            )
        ],
        b=[
            CircleShape(
                name="release_holes",
                layer="device",
                x="-plate/2 + pitch/2",
                y="-plate/2 + pitch/2",
                radius="hole_r",
                repeat=Repeat(
                    columns="floor(plate/pitch)",
                    rows="floor(plate/pitch)",
                    dx="pitch",
                    dy="pitch",
                ),
            )
        ],
    )
)
comb = {"fingers": 16, "finger_width": "w_finger", "gap": "w_finger"}
design.add(Instance("comb_top", "comb_drive", comb, y="plate/2 + 39", rotation=180))
design.add(Instance("comb_bottom", "comb_drive", comb, y="-plate/2 - 39"))
for side, sign in (("l", -1), ("r", 1)):
    x = f"{sign} * (plate/2 + 38.5)"
    design.add(Instance(f"spring_{side}", "serpentine_spring", {"turns": 3}, x=x, y=-54))
    design.add(Instance(f"anchor_{side}", "anchor", {"size": 40}, x=x, y=73))

save(design, "resonator.mems")
print("wrote resonator.mems")
