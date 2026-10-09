"""Exporters declare their options; the API, the CLI and the export dialog
take them from the declaration (requirement OUT-8)."""

from pathlib import Path
from typing import ClassVar

import klayout.db as kdb
import pytest

from mems_sketch.cli import main
from mems_sketch.export import base
from mems_sketch.export.base import ExportOption, export, options_of, resolve_options
from mems_sketch.export.klayout_formats import GdsExporter
from mems_sketch.storage import load

EXAMPLES = Path(__file__).parent.parent / "examples"
SIZE = ExportOption("size", 2.5, "Size", minimum=0, maximum=10, suffix=" µm")
COUNT = ExportOption("count", 3, "Count", minimum=1)
FLAG = ExportOption("flag", False, "Flag")
MODE = ExportOption("mode", "fast", "Mode", choices=("fast", "exact"))


class _Probe:
    """A plugin that records what it was given."""

    format_name = "probe"
    title = "Probe format"
    file_extension = ".probe"
    options: ClassVar = (SIZE, COUNT, FLAG, MODE)
    received: ClassVar[dict] = {}

    def export(self, project, geometry, path, **options):
        type(self).received = options
        Path(path).write_text("probe")


@pytest.fixture
def probe(monkeypatch):
    monkeypatch.setitem(base._runtime, "probe", _Probe)
    _Probe.received = {}
    return _Probe


@pytest.fixture(scope="module")
def resonator():
    return load(EXAMPLES / "resonator")


def test_options_are_checked_and_defaulted():
    assert resolve_options(_Probe, None) == {"size": 2.5, "count": 3, "flag": False, "mode": "fast"}
    assert resolve_options(_Probe, {"size": 4, "mode": "exact"})["size"] == 4.0
    with pytest.raises(ValueError, match="no option 'colour'.*size, count, flag, mode"):
        resolve_options(_Probe, {"colour": 1})
    with pytest.raises(ValueError, match="at most 10"):
        resolve_options(_Probe, {"size": 11})
    with pytest.raises(ValueError, match="whole number"):
        resolve_options(_Probe, {"count": 1.5})
    with pytest.raises(ValueError, match="yes or no"):
        resolve_options(_Probe, {"flag": 1})
    with pytest.raises(ValueError, match="one of fast, exact"):
        resolve_options(_Probe, {"mode": "slow"})


def test_options_parse_text():
    assert SIZE.parse("1.5") == 1.5
    assert COUNT.parse("7") == 7
    assert FLAG.parse("yes") is True and FLAG.parse("off") is False
    with pytest.raises(ValueError, match="is a float, not 'wide'"):
        SIZE.parse("wide")
    with pytest.raises(ValueError, match="is an int"):
        COUNT.parse("2.5")


def test_a_plugin_gets_its_options(probe, resonator, tmp_path):
    export(resonator, tmp_path / "out.probe", options={"count": 5})
    assert probe.received == {"size": 2.5, "count": 5, "flag": False, "mode": "fast"}


def test_gds_on_a_coarser_grid(resonator, tmp_path):
    path = export(resonator, tmp_path / "out.gds", options={"grid_um": 0.01, "top_cell": "CHIP"})
    layout = kdb.Layout()
    layout.read(str(path))
    assert layout.dbu == pytest.approx(0.01)
    assert layout.top_cell().name == "CHIP"
    fine = export(resonator, tmp_path / "fine.gds")
    reference = kdb.Layout()
    reference.read(str(fine))
    assert reference.dbu == pytest.approx(0.001)
    coarse_area = layout.top_cell().dbbox().area()
    assert coarse_area == pytest.approx(reference.top_cell().dbbox().area(), rel=1e-3)


def test_the_grid_is_a_whole_multiple_of_1_nm(resonator, tmp_path):
    with pytest.raises(ValueError, match="whole multiple of 1 nm"):
        export(resonator, tmp_path / "out.gds", options={"grid_um": 0.0015})


def test_builtin_formats_declare_options():
    assert [o.name for o in options_of(GdsExporter)] == ["grid_um", "top_cell"]
    assert base.title_of(GdsExporter) == "GDSII"


def test_the_cli_takes_options(probe, tmp_path, capsys):
    project = str(EXAMPLES / "resonator")
    assert (
        main(["export", project, str(tmp_path / "a.probe"), "-O", "size=7", "-O", "flag=yes"]) == 0
    )
    assert probe.received["size"] == 7.0 and probe.received["flag"] is True
    assert main(["export", project, str(tmp_path / "b.probe"), "--option", "colour=red"]) == 2
    assert "no option 'colour'" in capsys.readouterr().err
    assert main(["export", project, str(tmp_path / "c.probe"), "-O", "size=99"]) == 2
    assert "at most 10" in capsys.readouterr().err


def test_the_cli_lists_formats_and_options(probe, capsys):
    assert main(["formats"]) == 0
    out = capsys.readouterr().out
    assert "gds      .gds   GDSII" in out
    assert "grid_um=0.001" in out
    assert "probe" in out and "mode=fast" in out
