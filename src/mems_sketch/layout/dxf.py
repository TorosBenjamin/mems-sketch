"""DXF files, written (R12 entities, as most tools read): each cell a block,
each polygon a closed polyline (cut into pieces without holes), each
placement an INSERT. Coordinates in µm; layers named ``L<layer>D<datatype>``
or by their name."""

from __future__ import annotations

from mems_sketch.layout.model import Layout, Placement, hole_free


def write(layout: Layout) -> bytes:
    lines: list[str] = []

    def put(code: int, value: object) -> None:
        lines.append(str(code))
        lines.append(f"{value:.9g}" if isinstance(value, float) else str(value))

    def name_of(layer: tuple[int, int]) -> str:
        return layout.layer_names.get(layer) or f"L{layer[0]}D{layer[1]}"

    layers = sorted({layer for cell in layout.cells.values() for layer in cell.polygons})
    put(0, "SECTION")
    put(2, "HEADER")
    put(9, "$INSUNITS")
    put(70, 13)  # micrometres
    put(0, "ENDSEC")
    put(0, "SECTION")
    put(2, "TABLES")
    put(0, "TABLE")
    put(2, "LAYER")
    put(70, len(layers))
    for layer in layers:
        put(0, "LAYER")
        put(2, name_of(layer))
        put(70, 0)
        put(62, 7)
        put(6, "CONTINUOUS")
    put(0, "ENDTAB")
    put(0, "ENDSEC")
    put(0, "SECTION")
    put(2, "BLOCKS")
    for cell in layout.cells.values():
        put(0, "BLOCK")
        put(8, "0")
        put(2, cell.name)
        put(70, 0)
        put(10, 0.0)
        put(20, 0.0)
        put(30, 0.0)
        put(3, cell.name)
        for layer, polygons in cell.polygons.items():
            for polygon in polygons:
                for piece in hole_free(polygon):
                    _polyline(put, name_of(layer), piece.hull, layout.dbu)
        for placement in cell.placements:
            _inserts(put, placement, layout.dbu)
        put(0, "ENDBLK")
        put(8, "0")
    put(0, "ENDSEC")
    put(0, "SECTION")
    put(2, "ENTITIES")
    for name in layout.top_cells():
        _inserts(put, Placement(name), layout.dbu)
    put(0, "ENDSEC")
    put(0, "EOF")
    return ("\n".join(lines) + "\n").encode("ascii", "replace")


def _polyline(put, layer: str, ring: list[tuple[int, int]], dbu: float) -> None:
    put(0, "POLYLINE")
    put(8, layer)
    put(66, 1)
    put(70, 1)  # closed
    for x, y in ring:
        put(0, "VERTEX")
        put(8, layer)
        put(10, x * dbu)
        put(20, y * dbu)
    put(0, "SEQEND")
    put(8, layer)


def _inserts(put, p: Placement, dbu: float) -> None:
    """One INSERT per copy (DXF arrays step along the insert's own rotated axes)."""
    for x, y in p.copies():
        put(0, "INSERT")
        put(8, "0")
        put(2, p.cell)
        put(10, x * dbu)
        put(20, y * dbu)
        if p.mag != 1 or p.mirror:
            put(41, float(p.mag))
            put(42, float(-p.mag if p.mirror else p.mag))
        if p.angle:
            put(50, float(p.angle))
