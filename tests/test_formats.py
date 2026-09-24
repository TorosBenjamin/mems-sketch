"""One-file formats (YAML, JSON, XML, MATLAB .mat): whole projects both ways,
and geometry out and back in as a component."""

import builtins
import importlib.util
import shutil
from pathlib import Path

import pytest

from mems_sketch import Layer, ParamDef, Project, RectShape, load, save
from mems_sketch.cli import main as cli
from mems_sketch.core.shapes import BooleanShape, CircleShape
from mems_sketch.core.user_component import PointDef
from mems_sketch.editing import EditSession
from mems_sketch.export.base import export
from mems_sketch.storage import formats
from mems_sketch.storage.document import (
    DocumentError,
    geometry_from_data,
    read_geometry,
    read_project,
    write_project,
)
from mems_sketch.storage.formats import Matrix, MissingDependency

EXAMPLE = Path(__file__).parent.parent / "examples" / "resonator"
needs_scipy = pytest.mark.skipif(
    importlib.util.find_spec("scipy") is None, reason=".mat files need scipy"
)
MAT = pytest.param(".mat", marks=needs_scipy)
SUFFIXES = [".yaml", ".json", ".xml", MAT]


TREE = {
    "format": "test/1",
    "text": "a b <c> & ü",
    "empty_text": "",
    "whole": 20,
    "fraction": 0.125,
    "negative": -3.5e-9,
    "yes": True,
    "no": False,
    "nothing": None,
    "list": [1, "two", [3, 4], {"k": "v"}, []],
    "single": ["only"],
    "empty_list": [],
    "empty_map": {},
    "odd_keys": {"comb/finger": 1, "5/0": "device", "with space": None, "keys": 2},
    "nested": {"a": {"b": {"c": [None, True]}}},
    "blob": b"\x00\x01binary\xff",
    "matrix": Matrix.of([(0, 0), (10, 0), (10, 4.5)]),
}


def plain(tree):
    """What a format without matrices gives back."""
    if isinstance(tree, Matrix):
        return tree.tolist()
    if isinstance(tree, dict):
        return {k: plain(v) for k, v in tree.items()}
    if isinstance(tree, list):
        return [plain(v) for v in tree]
    return tree


@pytest.mark.parametrize("name", ["yaml", "json", "xml", pytest.param("mat", marks=needs_scipy)])
def test_every_codec_reads_back_what_it_wrote(name):
    codec = formats.codecs()[name]
    back = codec.load(codec.dump(TREE))
    expected = TREE if name in ("xml", "mat") else plain(TREE)
    assert back == expected


def design() -> Project:
    project = Project(name="demo")
    project.add_layer(Layer("device", 1, min_width=2))
    project.add_layer(Layer("metal", 5, 2))
    project.process.constants["undercut"] = 0.5
    project.components["top"].parameters.append(ParamDef(name="pitch", default=20, min=5))
    beam = RectShape(name="beam", layer="device", x0=0, y0=0, x1="pitch * 5", y1=10)
    hole = CircleShape(name="hole", layer="device", x=20, y=5, radius=2)
    project.add(BooleanShape(name="cut", op="subtract", a=[beam], b=[hole]))
    project.add(RectShape(layer="metal", x0=0, y0=20, x1=10, y1=30, enabled=False))
    return project


@pytest.mark.parametrize("suffix", SUFFIXES)
def test_a_project_goes_to_one_file_and_back(tmp_path, suffix, write_gds):
    project = design()
    session = EditSession(project)
    session.imports.add(write_gds(tmp_path / "frame.gds", [(1, 0, 0, 0, 50, 5)]))
    path = write_project(session.project, tmp_path / f"demo{suffix}")
    assert read_project(path) == session.project


@pytest.mark.parametrize("suffix", SUFFIXES)
def test_converting_a_folder_to_a_file_and_back_gives_the_same_files(tmp_path, suffix):
    document = tmp_path / f"resonator{suffix}"
    assert cli(["convert", str(EXAMPLE), str(document)]) == 0
    folder = EXAMPLE.parent / f".roundtrip-{suffix[1:]}"  # beside it: library paths match
    try:
        assert cli(["convert", str(document), str(folder)]) == 0
        original = {p.relative_to(EXAMPLE): p.read_bytes() for p in EXAMPLE.rglob("*.yaml")}
        again = {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*.yaml")}
        assert again == original
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_a_folder_is_not_overwritten_by_convert(tmp_path):
    save(design(), tmp_path / "there")
    assert cli(["convert", str(EXAMPLE), str(tmp_path / "there")]) == 2


def test_load_and_save_follow_the_name(tmp_path):
    save(design(), tmp_path / "demo.xml")
    assert (tmp_path / "demo.xml").is_file()
    assert load(tmp_path / "demo.xml") == design()


def test_a_session_opens_a_file_as_a_copy(tmp_path):
    save(design(), tmp_path / "demo.json")
    session = EditSession.open_project(tmp_path / "demo.json")
    assert session.path is None and session.dirty  # saved as a folder
    assert session.project == design()


def test_wrong_documents_say_what_they_are(tmp_path):
    (tmp_path / "x.json").write_text('{"format": "mems-sketch-geometry/1", "layers": {}}')
    with pytest.raises(DocumentError, match="geometry file"):
        load(tmp_path / "x.json")
    (tmp_path / "y.json").write_text("{not json")
    with pytest.raises(ValueError, match="not a readable JSON file"):
        load(tmp_path / "y.json")


# -- geometry -----------------------------------------------------------------------


@pytest.mark.parametrize("suffix", [".json", ".xml", MAT])
def test_geometry_out_and_back_in_as_a_component(tmp_path, suffix):
    project = design()
    project.components["top"].points.append(PointDef(name="tip", x="pitch * 5", y=5))
    path = export(project, tmp_path / f"beam{suffix}", params={"pitch": 30})
    geometry, numbers, component = read_geometry(path)
    assert component == "top" and numbers == {"device": (1, 0)}
    area = geometry.layers["device"].area()
    tree = formats.read(path)
    assert tree["parameters"] == {"pitch": 30}
    assert tree["points"]["tip"] == {"x": 150, "y": 5}
    assert len(tree["layers"]["device"]["polygons"]) == 1
    assert "holes" in tree["layers"]["device"]["polygons"][0]

    other = Project()
    other.add_layer(Layer("device", 1))
    session = EditSession(other)
    name = session.imports.add(path)
    session.nodes.add_component(name)
    assert session.results.geometry().layers["device"].area() == area


@needs_scipy
def test_geometry_written_by_matlab_can_be_imported(tmp_path):
    """What a MATLAB script saves: numbers as doubles, polygons as N×2 matrices."""
    tree = {
        "format": "mems-sketch-geometry/1",
        "layers": {
            "device": {
                "gds": Matrix.of([(1, 0)]),
                "polygons": [Matrix.of([(0, 0), (10, 0), (10, 10), (0, 10)])],
            },
            "trench": {"polygons": {"hull": Matrix.of([(0, 20), (5, 20), (5, 25)])}},
        },
    }
    path = formats.write(tmp_path / "from_matlab.mat", tree)
    project = Project()
    project.add_layer(Layer("device", 1))
    session = EditSession(project)
    name = session.imports.add(path)
    assert session.project.imports[name].layers == {"1/0": "device", "2/0": "trench"}
    session.nodes.add_component(name)
    layers = session.results.geometry().layers
    assert layers["device"].area() == 100 * 1e6
    assert layers["trench"].area() == 12.5 * 1e6


def test_a_bad_geometry_document_is_refused():
    with pytest.raises(DocumentError, match="project file"):
        geometry_from_data({"format": "mems-sketch/1"})
    with pytest.raises(DocumentError, match="unit"):
        geometry_from_data({"format": "mems-sketch-geometry/1", "unit": "mm"})


def test_mat_files_without_scipy_say_what_to_install(monkeypatch, tmp_path):
    real = builtins.__import__

    def no_scipy(name, *args, **kwargs):
        if name.startswith("scipy"):
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_scipy)
    with pytest.raises(MissingDependency, match="mems-sketch\\[matlab\\]"):
        save(design(), tmp_path / "demo.mat")


def test_the_cli_exports_geometry_documents(tmp_path):
    out = tmp_path / "resonator.json"
    assert cli(["export", str(EXAMPLE), str(out)]) == 0
    tree = formats.read(out)
    assert tree["format"] == "mems-sketch-geometry/1" and tree["layers"]
