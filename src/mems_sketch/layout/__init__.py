"""Layout files without a layout library: GDSII read and written, OASIS and
DXF written, from one small in-memory model (:mod:`.model`).

- :mod:`.gds`: GDSII (``.gds``), read and write: imports and exports.
- :mod:`.oasis`: OASIS (``.oas``), write.
- :mod:`.dxf`: DXF (``.dxf``), write.

None of these formats has polygons with holes, so polygons are cut into
pieces without holes first (:func:`.model.hole_free`).
"""

from mems_sketch.layout.model import Cell, Layout, Placement, hole_free

__all__ = ["Cell", "Layout", "Placement", "hole_free", "read", "write"]


def write(layout: Layout, path, file_format: str | None = None) -> None:
    """Write ``layout`` as GDS2, OASIS or DXF (``file_format``, else by the suffix)."""
    from pathlib import Path

    from mems_sketch.layout import dxf, gds, oasis

    path = Path(path)
    writers = {"GDS2": gds.write, "OASIS": oasis.write, "DXF": dxf.write}
    fmt = file_format or {".gds": "GDS2", ".oas": "OASIS", ".dxf": "DXF"}.get(path.suffix.lower())
    if fmt not in writers:
        raise ValueError(f"no layout format for '{path.suffix}' files")
    path.write_bytes(writers[fmt](layout))


def read(data: bytes) -> Layout:
    """A GDSII file's layout (OASIS and DXF are not read)."""
    from mems_sketch.layout import gds

    if data[:11] == b"%SEMI-OASIS":
        raise ValueError("OASIS files cannot be imported: save the cell as GDS")
    return gds.read(data)
