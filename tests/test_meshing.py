"""Meshing (#49 in 2D, #50, #51, #52): mesh settings as component data, graded
conforming meshes from gmsh, and the solver formats."""

import xml.etree.ElementTree as ET

import numpy as np
import pytest

from mems_sketch import ParamDef, RectShape
from mems_sketch.core.component import Geometry
from mems_sketch.core.diff import diff_projects
from mems_sketch.core.mesh_settings import MeshSettings, refinement
from mems_sketch.core.project import new_project
from mems_sketch.storage import load, save

# -- settings as project data (#50) -------------------------------------------------


def chip(mesh: MeshSettings | None = None):
    project = new_project("chip")
    top = project.components["top"]
    top.parameters = [ParamDef(name="w", default=200), ParamDef(name="fine", default=2)]
    top.shapes = [
        RectShape(name="frame", layer="device", x0=0, y0=0, x1="w", y1=100),
        RectShape(name="pad", layer="anchor", x0=80, y0=30, x1=120, y1=70),
    ]
    if mesh is not None:
        top.mesh = mesh
    return project


def test_settings_are_saved_read_back_and_left_out_when_default(tmp_path):
    mesh = MeshSettings(
        size="w / 10",
        growth=1.3,
        layers={"anchor": "fine"},
        refine=[
            refinement(kind="point", size=1, x=100, y="50", radius=3),
            refinement(kind="edges", layer="device", size=0.5, distance=1),
        ],
    )
    folder = save(chip(mesh), tmp_path / "chip")
    text = next(folder.rglob("top.msketch")).read_text()
    assert "mesh:" in text and "kind: point" in text and "growth: 1.3" in text
    assert load(folder).components["top"].mesh == mesh
    plain = save(chip(), tmp_path / "plain")
    assert "mesh" not in next(plain.rglob("top.msketch")).read_text()


def test_settings_are_expressions_and_checked():
    resolved = MeshSettings(size="w / 10", layers={"anchor": "fine"}).resolved(
        {"w": 200, "fine": 2}, (0, 0, 200, 100)
    )
    assert (resolved.size, resolved.layers) == (20, {"anchor": 2})
    automatic = MeshSettings().resolved({}, (0, 0, 200, 100))
    assert automatic.size == 5  # the smaller side over 20
    with pytest.raises(ValueError, match="growth must be more than 1"):
        MeshSettings(growth=1).resolved({}, None)
    with pytest.raises(ValueError, match="size must be more than 0"):
        MeshSettings(size="w - 200").resolved({"w": 200}, None)
    with pytest.raises(ValueError, match="layer"):
        refinement(kind="edges", size=1)  # names the layer whose edges it refines
    assert automatic.ramp(1, 5) == pytest.approx(20)  # (5 - 1) / (1.2 - 1)


def test_settings_changes_are_in_the_diff_and_follow_renames():
    old = chip(MeshSettings(size=10))
    new = chip(MeshSettings(size=8, refine=[refinement(kind="point", size=1)]))
    (change,) = [c for c in diff_projects(old, new) if c.what == "mesh"]
    assert "size 10 → 8" in change.text and "refinement point (0, 0) added" in change.text
    project = chip(MeshSettings(size="w / 10", layers={"anchor": "fine"}))
    project.components["top"].rename_parameter("fine", "pad_size")
    assert project.components["top"].mesh.layers == {"anchor": "pad_size"}


# -- meshes (gmsh) --------------------------------------------------------------------

meshing = pytest.importorskip("mems_sketch.meshing")
needs_gmsh = pytest.mark.skipif(not meshing.available(), reason="gmsh is not installed")


def frame_and_pad() -> Geometry:
    geometry = Geometry()
    geometry.add_rect("device", 0, 0, 200, 100)
    geometry.add_rect("anchor", 80, 30, 120, 70)
    return geometry


def neighbour_ratios(mesh) -> np.ndarray:
    """For each edge two triangles share, the larger one's size over the smaller's."""
    owners: dict = {}
    for k, triangle in enumerate(mesh.elements.tolist()):
        for a, b in ((0, 1), (1, 2), (2, 0)):
            key = tuple(sorted((triangle[a], triangle[b])))
            owners.setdefault(key, []).append(k)
    sizes = mesh.sizes()
    pairs = np.array([v for v in owners.values() if len(v) == 2])
    a, b = sizes[pairs[:, 0]], sizes[pairs[:, 1]]
    return np.maximum(a, b) / np.minimum(a, b)


@needs_gmsh
def test_layers_are_named_regions_sharing_their_nodes():
    mesh = meshing.generate(frame_and_pad(), MeshSettings(size=10))
    assert set(mesh.regions) == {"device", "anchor"}
    assert set(mesh.boundaries) == {"device_boundary", "anchor_boundary"}
    assert set(mesh.regions["anchor"]) <= set(mesh.regions["device"])  # the pad is inside
    assert mesh.element_count == len(mesh.regions["device"])  # one mesh, not two
    names, body = mesh.bodies()
    assert names == ["anchor+device", "device"] and len(body) == mesh.element_count
    area = 0.5 * mesh.sizes() ** 2
    assert area.sum() == pytest.approx(200 * 100, rel=1e-6)
    assert area[mesh.regions["anchor"]].sum() == pytest.approx(40 * 40, rel=1e-6)
    assert mesh.min_quality > 0.3


@needs_gmsh
def test_a_layers_size_applies_on_it_and_sizes_grade_without_a_seam():
    graded = meshing.generate(frame_and_pad(), MeshSettings(size=20, layers={"anchor": 1}))
    sizes = graded.sizes()
    on_pad = sizes[graded.regions["anchor"]]
    assert np.median(on_pad) == pytest.approx(1, rel=0.35)
    assert sizes.max() > 10  # coarse away from it
    ratios = neighbour_ratios(graded)
    assert np.percentile(ratios, 99) < 1.45 and ratios.max() < 2.0  # growth 1.2, no jump
    jumpy = meshing.generate(frame_and_pad(), MeshSettings(size=20, growth=4, layers={"anchor": 1}))
    assert neighbour_ratios(jumpy).max() > ratios.max()  # what the growth rate prevents
    assert jumpy.element_count < graded.element_count


@needs_gmsh
def test_refinements_make_the_mesh_finer_where_they_are():
    def mean_size_near(mesh, x, y, reach):
        centres = mesh.nodes[mesh.elements].mean(axis=1)
        near = np.hypot(centres[:, 0] - x, centres[:, 1] - y) < reach
        return mesh.sizes()[near].mean()

    plain = meshing.generate(frame_and_pad(), MeshSettings(size=10))
    for place in (
        refinement(kind="point", size=0.5, x=20, y=20, radius=2),
        refinement(kind="box", size=0.5, x0=15, y0=15, x1=25, y1=25),
        refinement(kind="edges", layer="anchor", size=0.5),
    ):
        fine = meshing.generate(frame_and_pad(), MeshSettings(size=10, refine=[place]))
        x, y = (80, 50) if place.label.startswith("edges") else (20, 20)
        assert mean_size_near(fine, x, y, 6) < 0.5 * mean_size_near(plain, x, y, 6)


@needs_gmsh
def test_settings_that_cannot_mesh_say_why():
    with pytest.raises(meshing.MeshError, match="layers that are not drawn: metal"):
        meshing.generate(frame_and_pad(), MeshSettings(layers={"metal": 1}))
    with pytest.raises(meshing.MeshError, match="nothing to mesh"):
        meshing.generate(Geometry(), MeshSettings())
    with pytest.raises(meshing.MeshError, match="growth"):
        meshing.generate(frame_and_pad(), MeshSettings(growth=0.5))
    only_pad = meshing.generate(frame_and_pad(), MeshSettings(size=10), layers=["anchor"])
    assert set(only_pad.regions) == {"anchor"}


# -- formats (#51) --------------------------------------------------------------------


@needs_gmsh
@pytest.mark.parametrize("extension", [".msh", ".inp", ".bdf", ".xdmf", ".elmer"])
def test_every_format_is_written_with_the_named_regions(tmp_path, extension):
    from mems_sketch.export.base import export

    path = export(chip(MeshSettings(size=20)), tmp_path / f"chip{extension}")
    if extension == ".elmer":
        files = {p.name: p.read_text() for p in path.iterdir()}
        header = files["mesh.header"].split()
        nodes, elements, boundary = (int(n) for n in header[:3])
        assert len(files["mesh.nodes"].splitlines()) == nodes
        assert len(files["mesh.elements"].splitlines()) == elements
        assert len(files["mesh.boundary"].splitlines()) == boundary > 0
        assert "$ anchor_and_device = 1" in files["mesh.names"]
        assert "$ device_boundary" in files["mesh.names"]
        return
    text = path.read_text()
    if extension == ".msh":
        assert '"device"' in text and '"anchor_boundary"' in text
    elif extension == ".inp":
        assert "ELSET=device" in text and "type=CPS3" in text
    elif extension == ".bdf":
        assert "CTRIA3" in text
    else:
        grid = ET.fromstring(text).find("Domain/Grid")
        assert "anchor+device" in grid.find("Information").get("Value")
        assert int(grid.find("Topology").get("NumberOfElements")) > 10


@needs_gmsh
def test_units_and_layers_are_options(tmp_path):
    from mems_sketch.export.base import export

    metres = export(chip(MeshSettings(size=20)), tmp_path / "m.elmer", options={"units": "m"})
    xs = [float(line.split()[2]) for line in (metres / "mesh.nodes").read_text().splitlines()]
    assert max(xs) == pytest.approx(200e-6)
    pad = export(chip(MeshSettings(size=20)), tmp_path / "pad.xdmf", options={"layers": "anchor"})
    assert "device" not in ET.parse(pad).find("Domain/Grid/Information").get("Value")


def test_the_mesh_formats_are_exporters():
    from mems_sketch.export.base import available_exporters

    assert {"msh", "inp", "bdf", "elmer", "xdmf"} <= set(available_exporters())


# -- 3D: the layers extruded through the stack (#49) -----------------------------------

STACK = {"anchor": (0.0, 2.0), "device": (2.0, 3.0)}  # the anchor under the device


@needs_gmsh
def test_a_3d_mesh_extrudes_each_layer_and_joins_them_node_to_node():
    mesh = meshing.generate(frame_and_pad(), MeshSettings(size=10), heights=STACK)
    assert mesh.dim == 3 and mesh.elements.shape[1] == 4
    volume = mesh.measures()
    assert volume[mesh.regions["device"]].sum() == pytest.approx(200 * 100 * 3, rel=1e-6)
    assert volume[mesh.regions["anchor"]].sum() == pytest.approx(40 * 40 * 2, rel=1e-6)
    assert not set(mesh.regions["anchor"]) & set(mesh.regions["device"])  # one above the other
    on_anchor = set(mesh.elements[mesh.regions["anchor"]].ravel())
    on_device = set(mesh.elements[mesh.regions["device"]].ravel())
    shared = on_anchor & on_device
    assert shared and np.allclose(mesh.nodes[list(shared), 2], 2.0)  # the face they share
    assert set(mesh.boundaries) == {"device_boundary", "anchor_boundary"}
    assert mesh.facets.shape[1] == 3 and "tetrahedra" in mesh.summary()


@needs_gmsh
def test_a_3d_mesh_leaves_out_layers_without_a_thickness():
    mesh = meshing.generate(frame_and_pad(), MeshSettings(size=10), heights={"device": (2.0, 3.0)})
    assert set(mesh.regions) == {"device"}
    with pytest.raises(meshing.MeshError, match="with a thickness"):
        meshing.generate(frame_and_pad(), MeshSettings(), heights={"metal": (0, 1)})


@needs_gmsh
@pytest.mark.parametrize("extension", [".msh", ".inp", ".xdmf", ".elmer"])
def test_3d_formats(tmp_path, extension):
    from mems_sketch.export.base import export

    project = chip(MeshSettings(size=20))
    project.layers["device"].z, project.layers["device"].thickness = "process.t", 3
    project.layers["anchor"].z, project.layers["anchor"].thickness = 0, "process.t"
    project.process.constants["t"] = 2.0
    path = export(project, tmp_path / f"chip{extension}", options={"dimensions": "3D"})
    if extension == ".elmer":
        header = (path / "mesh.header").read_text().split()
        assert "504" in header and "303" in header  # tetrahedra and their triangles
        z = [float(line.split()[4]) for line in (path / "mesh.nodes").read_text().splitlines()]
        assert max(z) == pytest.approx(5)
        return
    text = path.read_text()
    if extension == ".msh":
        assert '"device"' in text and '"anchor_boundary"' in text
    elif extension == ".inp":
        assert "type=C3D4" in text and "ELSET=device" in text
    else:
        grid = ET.fromstring(text).find("Domain/Grid")
        assert grid.find("Topology").get("TopologyType") == "Tetrahedron"
        assert grid.find("Geometry").get("GeometryType") == "XYZ"


@needs_gmsh
def test_3d_needs_heights_in_the_process(tmp_path):
    from mems_sketch.export.base import export

    project = chip(MeshSettings(size=20))
    for layer in project.layers.values():
        layer.thickness = None
    with pytest.raises(meshing.MeshError, match="z and thickness"):
        export(project, tmp_path / "chip.msh", options={"dimensions": "3D"})


# -- layer heights in the process -------------------------------------------------------


def test_layer_heights_are_saved_evaluated_and_checked(tmp_path):
    from mems_sketch.core.process import Layer

    project = chip()
    process = project.process
    process.constants.update({"t_ox": 2.0, "t_dev": 3.0})
    process.layers["anchor"].z, process.layers["anchor"].thickness = 0, "process.t_ox"
    process.layers["device"].z = "process.t_ox"
    process.layers["device"].thickness = "process.t_dev"
    heights = process.heights()
    assert heights["anchor"] == (0, 2) and heights["device"] == (2, 3)
    assert "metal" not in heights or process.layers["metal"].thickness is not None
    process.layers["metal"] = Layer("metal", 3, 0, None, 5, "process.t_dev - 3")
    with pytest.raises(ValueError, match="layer 'metal': thickness 0"):
        process.heights()


def test_the_bundled_processes_have_heights():
    from mems_sketch.libraries import bundled_library

    processes = bundled_library("mems").processes
    surface = processes["surface"].heights()
    assert surface["device"] == (2.5, 2.0) and surface["anchor"] == (0.5, 2.0)
    assert processes["soi"].heights()["device"] == (2.0, 25.0)
    assert "cavity" not in processes["soi"].heights()  # a mask, not extruded
    poly = processes["polysilicon"].heights()
    assert poly["poly2"][0] == pytest.approx(poly["via12"][0] + poly["via12"][1])


def test_layer_heights_round_trip_and_show_in_diffs(tmp_path):
    project = chip()
    project.process.constants["t"] = 2.0
    project.layers["device"].z, project.layers["device"].thickness = 1, "process.t"
    folder = save(project, tmp_path / "chip")
    loaded = load(folder)
    assert (loaded.layers["device"].z, loaded.layers["device"].thickness) == (1, "process.t")
    changed = load(folder)
    changed.layers["device"].thickness = 4.0
    texts = [c.text for c in diff_projects(loaded, changed)]
    assert any("thickness process.t → 4" in t for t in texts), texts
