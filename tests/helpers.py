"""Helpers for tests that read layout files."""


def read_gds(path):
    """A GDS file's layout (mems_sketch.layout)."""
    from mems_sketch.layout import gds

    return gds.read(path.read_bytes() if hasattr(path, "read_bytes") else open(path, "rb").read())


def flat_box(layout, cell=None):
    """The box around everything in a cell (default: the top one), flattened: µm."""
    from mems_sketch.core.region import Box, Region

    cell = cell or layout.top_cells()[0]
    box = Box()
    for gds_layer in layout.layers_in(cell):
        box = box + Region.from_polygons(layout.flat(cell, gds_layer)).bbox()
    return tuple(v * layout.dbu for v in (box.left, box.bottom, box.right, box.top))
