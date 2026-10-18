"""The bundled mems library's example processes (#178): each has rules that a
small sound design passes, and each rule catches what it is for."""

import pytest

from mems_sketch import RectShape
from mems_sketch.core.project import Project
from mems_sketch.libraries import bundled_library
from mems_sketch.process.rules import check


def r(layer, x0, y0, x1, y1):
    return RectShape(layer=layer, x0=x0, y0=y0, x1=x1, y1=y1)


def on(process: str, *shapes) -> Project:
    project = Project(name="t", process_name=f"mems.{process}")
    project.use_process(f"mems.{process}")
    project.components["top"].shapes = list(shapes)
    return project


def broken(project: Project) -> set[str]:
    return {v.rule for v in check(project, component="top")}


# A sound design on each: an anchored pad holding a released beam, and what
# else the process has (poly2 over a via, metal on it; a back-side cavity).
SOUND = {
    "surface": [
        r("device", 0, 0, 20, 20),
        r("anchor", 1, 1, 19, 19),
        r("device", 20, 8, 120, 12),
    ],
    "polysilicon": [
        r("poly0", -10, -10, 130, 30),
        r("poly1", 0, 0, 20, 20),
        r("anchor1", 2, 2, 18, 18),
        r("poly1", 20, 8, 120, 12),
        r("poly2", 0, 40, 20, 60),
        r("anchor2", 2, 42, 18, 58),
        r("metal", 3, 43, 17, 57),
        r("poly2", 20, 48, 120, 52),
    ],
    "soi": [
        r("device", 0, 0, 100, 100),
        r("anchor", 0, 0, 100, 100),
        r("metal", 10, 10, 90, 90),
        r("device", 100, 46, 400, 54),
        r("cavity", 200, -100, 500, 200),
    ],
}


@pytest.mark.parametrize("process", sorted(SOUND))
def test_a_sound_design_passes(process):
    assert broken(on(process, *SOUND[process])) == set()


@pytest.mark.parametrize("process", sorted(SOUND))
def test_each_is_labelled_an_example(process):
    found = bundled_library("mems").processes[process]
    assert "example" in found.description.lower() and "not any foundry" in found.description


CASES = [  # process, what is added, the rule it breaks
    ("polysilicon", r("poly1", 0, 100, 1, 140), "poly1_min_width"),
    ("polysilicon", r("poly1", 121, 8, 122, 12), "poly1_min_space"),
    ("polysilicon", r("poly0", -10, 31, 130, 32), "poly0_min_width"),
    ("polysilicon", r("anchor1", 0.5, 0.5, 2.5, 2.5), "anchor1_min_width"),
    ("polysilicon", r("anchor1", 1, 1, 5, 5), "anchor1_in_poly1"),
    ("polysilicon", r("dimple", 50, 8.5, 52, 11.5), "dimple_in_poly1"),
    ("polysilicon", r("via12", 60, 9, 62, 11), "via12_in_poly1"),
    ("polysilicon", r("anchor2", 1, 41, 5, 45), "anchor2_in_poly2"),
    ("polysilicon", r("metal", 0, 50, 20, 56), "metal_on_poly2"),
    ("polysilicon", r("poly1", 200, 0, 220, 20), "poly1_anchored"),
    ("polysilicon", r("poly1", 20, 0, 60, 20), "poly1_release"),
    ("polysilicon", r("poly2", 200, 40, 220, 60), "poly2_anchored"),
    ("soi", r("device", 0, 200, 2, 260), "device_min_width"),
    ("soi", r("device", 101, 56, 140, 60), "device_min_space"),
    ("soi", r("metal", 95, 10, 99, 90), "metal_min_width"),
    ("soi", r("anchor", 120, 60, 130, 70), "anchor_in_device"),
    ("soi", r("anchor", 160, 46, 170, 54), "cavity_clear_of_anchors"),
    ("soi", r("cavity", -200, -100, -150, 200), "cavity_min_width"),
    ("soi", r("device", 300, 300, 320, 320), "device_anchored"),
    ("soi", r("device", 100, 30, 200, 70), "device_release"),
]


@pytest.mark.parametrize("process, added, rule", CASES, ids=[c[2] for c in CASES])
def test_each_rule_catches_what_it_is_for(process, added, rule):
    assert rule in broken(on(process, *SOUND[process], added))


def test_the_mems_components_build_on_each_process():
    for process in SOUND:
        project = on(process)
        for name in ("anchor", "comb_drive", "serpentine_spring", "perforated_plate"):
            assert not project.render(f"mems.{name}").is_empty(), (process, name)
