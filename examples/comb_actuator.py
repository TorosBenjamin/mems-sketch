"""Code-first example: a comb actuator with anchors, exported with etch compensation.

Run with:  python examples/comb_actuator.py
"""

from mems_sketch import Design, Instance, Layer, export, save
from mems_sketch.process import etch, rules

design = Design(name="comb_actuator")
design.add_layer(Layer("device", 1, 0, undercut=0.3, min_width=1.5, min_space=1.5))
design.add_layer(Layer("anchor", 2, 0))

design.set_variable("w_finger", 2.0)
design.set_variable("gap", "w_finger")
design.set_variable("n_fingers", 20)

design.add_instance(
    Instance(
        "comb", "comb_drive", {"fingers": "n_fingers", "finger_width": "w_finger", "gap": "gap"}
    )
)
design.add_instance(Instance("spring_l", "serpentine_spring", {"turns": 2}, x=-100, y=37))
design.add_instance(Instance("spring_r", "serpentine_spring", {"turns": 2}, x=100, y=37))
design.add_instance(Instance("anchor_l", "anchor", {"size": 40}, x=-100, y=118))
design.add_instance(Instance("anchor_r", "anchor", {"size": 40}, x=100, y=118))

for violation in rules.check(design):
    print(f"[{violation.rule}] {violation.layer}: {violation.message} at {violation.bbox_um}")

save(design, "comb_actuator.mems")
export(design, "comb_actuator.gds", geometry=etch.compensated(design))
print("wrote comb_actuator.mems and comb_actuator.gds (etch-compensated)")
