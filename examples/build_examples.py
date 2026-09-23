"""Regenerates the example library and project from code.

    python examples/build_examples.py

writes examples/libraries/mems_std (a component library) and
examples/resonator (a project that uses it). Both are committed as YAML; this
script shows how the same files can be produced code-first.
"""

from pathlib import Path

from mems_sketch import (
    Align,
    BooleanShape,
    CircleShape,
    ComponentDef,
    Instance,
    ParamDef,
    Project,
    RectShape,
    Repeat,
    load_library,
    save,
)
from mems_sketch.core.process import default_process

HERE = Path(__file__).parent

# -- a reusable component library ------------------------------------------

library = Project(name="mems_std")
library.define_component(
    ComponentDef(
        name="perforated_plate",
        description="Square plate with a grid of round release holes",
        parameters=[
            ParamDef(name="size", default=160, min=10, description="Edge length (µm)"),
            ParamDef(name="pitch", default=20, min=2, description="Hole pitch (µm)"),
            ParamDef(name="hole_r", default="pitch / 6", min=0.5, description="Hole radius (µm)"),
        ],
        shapes=[
            BooleanShape(
                name="plate",
                op="subtract",
                a=[
                    RectShape(
                        name="outline",
                        layer="device",
                        x0="-size/2",
                        y0="-size/2",
                        x1="size/2",
                        y1="size/2",
                    )
                ],
                b=[
                    CircleShape(
                        name="holes",
                        layer="device",
                        x="-size/2 + pitch/2",
                        y="-size/2 + pitch/2",
                        radius="hole_r",
                        repeat=Repeat(
                            columns="floor(size/pitch)",
                            rows="floor(size/pitch)",
                            dx="pitch",
                            dy="pitch",
                        ),
                    )
                ],
            )
        ],
    )
)
library_folder = save(library, HERE / "libraries" / "mems_std")

# -- a project that uses the library ----------------------------------------

project = Project(name="resonator", process=default_process())
project.layers["device"].undercut = 0.3
project.process.constants["min_gap"] = 2
project.libraries["std"] = load_library("std", library_folder)

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
