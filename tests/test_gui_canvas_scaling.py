"""At a fractional scale (130 % on a laptop screen) the canvas draws its cached
pictures exactly where the shapes are: panning and zooming never tear them apart.

Qt's scale is fixed when the application starts, so the canvas runs in a process
of its own with QT_SCALE_FACTOR set."""

import json
import os
import subprocess
import sys
import textwrap

import pytest

pytest.importorskip("PySide6")

SCRIPT = textwrap.dedent(
    """
    import json, sys
    import numpy as np
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication, QGraphicsItem
    from mems_sketch.core.component import Geometry
    from mems_sketch.core.region import Region
    from mems_sketch.core.transform import Transform
    from mems_sketch.gui import canvas as module
    from mems_sketch.gui.canvas import LayoutCanvas

    app = QApplication(sys.argv)
    canvas = LayoutCanvas()
    canvas.configure(draft_quality=False)
    canvas.resize(500, 400)
    canvas.show()
    app.processEvents()

    holes = [
        [(x, y), (x, y + 2_000), (x + 2_000, y + 2_000), (x + 2_000, y)]
        for x in range(1_000, 40_000, 4_000)
        for y in range(1_000, 40_000, 4_000)
    ]
    hull = [(0, 0), (40_000, 0), (40_000, 40_000), (0, 40_000)]
    plate = Geometry()
    plate.layers["device"] = Region.from_polygons([(hull, holes)], merged=True)
    top = Geometry()
    for i in range(3):
        for j in range(3):
            top.place(plate, Transform(i * 40.0, j * 40.0))  # copies that touch
    canvas.show_geometry(top, {"device": QColor("#4c78a8")}, {})
    canvas.show_overlay(plate, [])
    canvas.fit()

    def grab():
        canvas.viewport().update()
        app.processEvents()
        image = canvas.viewport().grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
        bits = np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), -1)
        return bits[:, : image.width() * 4].astype(int).copy()

    def uncached():
        bands = (canvas._shape_band, canvas._highlight_band)
        items = [i for i in canvas.scene().items() if getattr(i, "band", None) in bands]
        no_contents = QGraphicsItem.GraphicsItemFlag.ItemHasNoContents
        for band in bands:
            band.hide()
        for item in items:
            item.setFlag(no_contents, False)
        image = grab()
        for band in bands:
            band.show()
        for item in items:
            item.setFlag(no_contents, True)
        return image

    def differing(a, b):
        return int((np.abs(a - b) > 8).any(axis=1).sum()) if a.shape == b.shape else -1

    dpr = canvas.viewport().devicePixelRatioF()
    whole = 10  # scrolling by this many pixels moves the view by whole device pixels at 1.3
    h, v = canvas.horizontalScrollBar(), canvas.verticalScrollBar()
    at = QPointF(170, 130)
    steps = [("zoom", 1.07)] * 3 + [("pan", (3, -2)), ("pan", (4, -3)), ("pan", (5, 2))] * 2
    steps += [("zoom", 1 / 1.13)] * 2
    worst = {"reused_vs_fresh": 0, "cached_vs_uncached": 0}
    for kind, arg in steps:
        if kind == "zoom":
            canvas._zoom_by_at(arg, at)
        else:
            h.setValue(h.value() - arg[0])
            v.setValue(v.value() - arg[1])
        reused = grab()
        module._Tiles._store.clear()
        module._Tiles._store_bytes = 0
        fresh = grab()
        worst["reused_vs_fresh"] = max(worst["reused_vs_fresh"], differing(reused, fresh))
        h.setValue(h.value() - h.value() % whole)
        v.setValue(v.value() - v.value() % whole)
        cached = grab()
        worst["cached_vs_uncached"] = max(worst["cached_vs_uncached"], differing(cached, uncached()))
    print(json.dumps({"dpr": dpr, **worst}))
    """
)


def test_pictures_meet_exactly_at_a_fractional_scale():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_SCALE_FACTOR="1.3")
    run = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout.strip().splitlines()[-1])
    assert result["dpr"] == pytest.approx(1.3)
    assert result["reused_vs_fresh"] == 0  # panning keeps the pictures in place
    assert result["cached_vs_uncached"] == 0  # and they are what drawing directly gives
