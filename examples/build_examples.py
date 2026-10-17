"""Regenerates the example library and project from code.

    python examples/build_examples.py

writes examples/libraries/mems_std (a library of components and a process)
and examples/resonator (a project that uses both). Both are committed as YAML; this
script shows how the same files can be produced code-first.
"""

from pathlib import Path

from mems_sketch import (
    Align,
    ComponentDef,
    Instance,
    ParamDef,
    PerforateModifier,
    Project,
    RectShape,
    load_library,
    save,
)
from mems_sketch.core.process import default_process

HERE = Path(__file__).parent

# -- a reusable component library ------------------------------------------

# A library: no top component. Its process, shared with the projects using it.
surface = default_process()
surface.description = "Single-layer surface micromachining"
surface.constants["min_gap"] = 2
library = Project(name="mems_std", top=None, process=surface, process_name="surface")
library.define_component(
    ComponentDef(
        name="perforated_plate",
        description="Square plate with a grid of release holes, round or rounded squares",
        parameters=[
            ParamDef(name="size", default=160, min=10, description="Edge length (µm)"),
            ParamDef(name="pitch", default=20, min=2, description="Hole pitch (µm)"),
            ParamDef(
                name="hole",
                default="pitch / 3",
                min=0.5,
                max="pitch",
                description="Hole size (µm), follows the pitch",
            ),
            ParamDef(
                name="corner_radius",
                default="hole / 2",
                min=0,
                description="Corner radius of the holes (µm): 0 square, half the size round",
            ),
            ParamDef(
                name="margin",
                default="(pitch - hole) / 2",
                internal=True,
                description="From the plate's edge to the holes (µm)",
            ),
        ],
        shapes=[
            RectShape(
                name="plate",
                layer="device",
                x0="-size/2",
                y0="-size/2",
                x1="size/2",
                y1="size/2",
                modifiers=[
                    PerforateModifier(
                        pitch="pitch",
                        hole_size="hole",
                        corner_radius="corner_radius",
                        margin="margin",
                    )
                ],
            )
        ],
    )
)
library_folder = save(library, HERE / "libraries" / "mems_std")

# -- a project that uses the library ----------------------------------------

project = Project(name="resonator", process_name="std.surface")
project.libraries["std"] = load_library("std", library_folder)
project.use_process("std.surface")
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
            Instance("spring", "serpentine_spring", {"turns": "turns"}),
            # The pad sits on the spring's last beam, overlapping it by 1 µm.
            Instance(
                "anchor",
                "anchor",
                {"size": 40},
                align=Align(point="bottom", to="spring.end", dy=-1),
            ),
        ],
    )
)

project.top_component.description = "Comb-driven resonator"
for name, value in {"plate": 160, "pitch": 20, "w_finger": "process.min_gap"}.items():
    project.set_variable(name, value)
comb = {"fingers": 16, "finger_width": "w_finger", "gap": "w_finger"}
project.add(Instance("mass", "std.perforated_plate", {"size": "plate", "pitch": "pitch"}))
# Positions come from alignments, so they follow any change of plate, comb or spring.
project.add(
    Instance(
        "comb_top",
        "comb_drive",
        comb,
        rotation=180,
        align=Align(point="moving", to="mass.top", dy=-1),
    )
)
project.add(
    Instance("comb_bottom", "comb_drive", comb, align=Align(point="moving", to="mass.bottom", dy=1))
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
print("wrote examples/libraries/mems_std and examples/resonator")
