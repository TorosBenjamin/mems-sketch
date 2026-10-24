"""Layout files without a layout library: GDSII read and written, OASIS and
DXF written, from one small in-memory model (:mod:`.model`).

- :mod:`.gds`: GDSII (``.gds``), read and write: imports and exports.
- :mod:`.oasis`: OASIS (``.oas``), write.
- :mod:`.dxf`: DXF (``.dxf``), write.

None of these formats has polygons with holes, so polygons are cut into
pieces without holes first (:func:`.model.hole_free`).
"""

import re
import warnings

from mems_sketch.layout.model import Cell, Layout, Placement, hole_free

__all__ = ["Cell", "CellNameWarning", "Layout", "Placement", "hole_free", "read", "write"]

# What every layout tool takes as a cell name: GDSII's own rule.
PORTABLE_NAME = re.compile(r"[A-Za-z0-9_?$]{1,32}")


class CellNameWarning(UserWarning):
    """A cell is written under a name some layout tools refuse or change."""


def check_cell_names(layout: Layout) -> None:
    """Warn (:class:`CellNameWarning`) of each cell name not every tool takes."""
    for name in layout.cells:
        if not PORTABLE_NAME.fullmatch(name):
            warnings.warn(
                CellNameWarning(
                    f"cell '{name}' is written as it is, but GDSII names are up to 32 "
                    "letters, digits, _, ? and $: some tools refuse or rename it "
                    "(the top_cell option names the top cell)"
                ),
                stacklevel=3,
            )


def write(layout: Layout, path, file_format: str | None = None) -> None:
    """Write ``layout`` as GDS2, OASIS or DXF (``file_format``, else by the suffix)."""
    from pathlib import Path

    from mems_sketch.layout import dxf, gds, oasis

    path = Path(path)
    writers = {"GDS2": gds.write, "OASIS": oasis.write, "DXF": dxf.write}
    fmt = file_format or {".gds": "GDS2", ".oas": "OASIS", ".dxf": "DXF"}.get(path.suffix.lower())
    if fmt not in writers:
        raise ValueError(f"no layout format for '{path.suffix}' files")
    check_cell_names(layout)
    path.write_bytes(writers[fmt](layout))


def read(data: bytes) -> Layout:
    """A GDSII file's layout (OASIS and DXF are not read)."""
    from mems_sketch.layout import gds

    if data[:11] == b"%SEMI-OASIS":
        raise ValueError("OASIS files are written, not read: save the cell as GDS to import it")
    if data.lstrip()[:1] == b"0" and b"SECTION" in data[:200]:
        raise ValueError("DXF files are written, not read: save the cell as GDS to import it")
    return gds.read(data)
