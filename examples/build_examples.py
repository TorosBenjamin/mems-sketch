"""Regenerates the example project from code.

    python examples/build_examples.py

writes examples/resonator, a project using the bundled ``mems`` library (its
components and its ``surface`` process). It is committed as YAML; this script
shows how the same files can be produced code-first.
"""

from pathlib import Path

from mems_sketch import (
    Align,
    ComponentDef,
    Instance,
    ParamDef,
    Project,
    save,
)

HERE = Path(__file__).parent

# -- a project that uses the bundled mems library ---------------------------

project = Project(name="resonator", process_name="mems.surface")
project.use_process("mems.surface")
# The drawing does not model its anchors or the etch, so the process's topology
# rules are turned off here; they stay listed, as off, with the reason.
for rule in ("device_anchored", "device_release"):
    project.process.rules[rule].enabled = False
    project.reasons[rule] = "the drawing does not model its anchors or the etch"

project.define_component(
    ComponentDef(
        name="suspension",
        description="Serpentine spring ending in an anchor pad",
        parameters=[ParamDef(name="turns", default=3, min=1, integer=True)],
        shapes=[
            Instance("spring", "mems.serpentine_spring", {"turns": "turns"}),
            # The pad sits on the spring's last beam, overlapping it by 1 µm.
            Instance(
                "anchor",
                "mems.anchor",
                {"size": 40},
                align=Align(point="bottom", to="spring.end", dy=-1),
            ),
        ],
    )
)

project.top_component.description = "Comb-driven resonator"
for name, value in {"plate": 162.5, "pitch": 10, "w_finger": "process.min_gap"}.items():
    project.set_variable(name, value)
comb = {"fingers": 16, "finger_width": "w_finger", "gap": "w_finger"}
project.add(Instance("mass", "mems.perforated_plate", {"size": "plate", "pitch": "pitch"}))
# Positions come from alignments, so they follow any change of plate, comb or spring.
project.add(
    Instance(
        "comb_top",
        "mems.comb_drive",
        comb,
        rotation=180,
        align=Align(point="moving", to="mass.top", dy=-1),
    )
)
project.add(
    Instance(
        "comb_bottom", "mems.comb_drive", comb, align=Align(point="moving", to="mass.bottom", dy=1)
    )
)
for side, point, edge, overlap in (
    ("left", "right", "left", 1.5),
    ("right", "left", "right", -1.5),
):
    project.add(
        Instance(
            f"suspension_{side}",
            "suspension",
            align=Align(point=point, to=f"mass.{edge}", dx=overlap),
        )
    )

save(project, HERE / "resonator")
print("wrote examples/resonator")
