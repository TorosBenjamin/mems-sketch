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
    "gds": "25f22814f7f30ee74cdf4def11e7431a40a59a3950ff17ac2a25321cf3236728",
    "oas": "b8928c16e738af65eb843ebf57f4a36c0476966688843ba585af0788d3f3ef1a",
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
