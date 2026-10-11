"""The same project gives the same files, byte for byte, on every run and every
platform (requirement QP-5).

The wheels workflow runs this on Linux, Windows and macOS. When the output is
meant to change (the example, the engine's geometry or a writer changed), run
it, check the new files, and put their hashes here.
"""

import hashlib
from pathlib import Path

import pytest

from mems_sketch.export.base import export
from mems_sketch.storage import load

EXAMPLE = Path(__file__).parent.parent / "examples" / "resonator"
EXPECTED = {
    "gds": "0b268c3ed3b1a4372be8859146e103b4e8d19dde5435468e9cbf92512e4875c5",
    "oas": "00485aefc978842aa038b6f7979e98ce182aa1de2522c4cfb1728429b353751c",
}


@pytest.mark.parametrize("suffix", sorted(EXPECTED))
def test_the_example_exports_byte_for_byte_the_same(suffix, tmp_path):
    first = export(load(EXAMPLE), tmp_path / f"a.{suffix}").read_bytes()
    second = export(load(EXAMPLE), tmp_path / f"b.{suffix}").read_bytes()
    assert first == second  # every run
    assert hashlib.sha256(first).hexdigest() == EXPECTED[suffix]  # every platform


def test_gds_files_carry_a_fixed_date_unless_source_date_epoch_says(monkeypatch):
    from mems_sketch.layout import gds

    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    assert gds._date() == (2000, 1, 1, 0, 0, 0)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    assert gds._date() == (2023, 11, 14, 22, 13, 20)
