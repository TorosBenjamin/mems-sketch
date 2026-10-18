"""Regenerate the bundled libraries' gallery pages and their pictures.

    python docs/gallery.py

writes docs/user/library-<name>.md for each bundled library (from the
components themselves: category, description, parameters, tags) and a picture
of each component at its defaults in docs/images/gallery/<library>/. Run it
after changing a bundled component and commit what changed; a test checks
that the pages are up to date.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mems_sketch.core.project import Library, Project
from mems_sketch.libraries import bundled_library, bundled_names

PAGES = ROOT / "docs" / "user"
IMAGES = ROOT / "docs" / "images" / "gallery"
# Where a component is drawn: the mems library's process, or the one that has
# the role it needs.
PROCESS = {"mems.dimple_array": "polysilicon"}
# Layers by what they are, the same in every picture.
COLOURS = {
    "device": "#4a7fd4",
    "poly1": "#4a7fd4",
    "metal": "#d29a2a",
    "anchor": "#c4473a",
    "anchor1": "#c4473a",
    "dimple": "#3da35d",
}
WIDTH, HEIGHT = 480, 320


def number(value) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def limits(parameter) -> str:
    parts = []
    if parameter.min is not None:
        parts.append(f"{'>' if parameter.min_exclusive else '≥'} {number(parameter.min)}")
    if parameter.max is not None:
        parts.append(f"{'<' if parameter.max_exclusive else '≤'} {number(parameter.max)}")
    if parameter.integer:
        parts.append("whole")
    return ", ".join(parts)


def _given(value) -> str:
    """A port's direction or width as written; inherited when not given."""
    return "*as at*" if value is None else f"`{number(value)}`"


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def categories(library: Library) -> list[str]:
    found = {d.category for d in library.components.values()}
    return sorted(
        found,
        key=lambda c: (library.categories.get(c, {}).get("order") or 99, library.category_title(c)),
    )


def page(name: str) -> str:
    """The gallery page of the bundled library ``name``."""
    library = bundled_library(name)
    lines = [
        f"# The `{name}` library",
        "",
        f"{library.description}. Every project has it: its components are placed as",
        f"`{name}.<component>`, read-only (**Copy into the project** makes an editable",
        "copy). Each is shown at its defaults.",
        "",
        "*This page is generated from the components by `python docs/gallery.py`.*",
        "",
    ]
    for category in categories(library):
        lines += [f"## {library.category_title(category)}", ""]
        about = library.categories.get(category, {}).get("description")
        if about:
            lines += [f"{about}.", ""]
        for component, definition in library.components.items():
            if definition.category != category:
                continue
            lines += [
                f"### `{name}.{component}`",
                "",
                f"![{name}.{component}](../images/gallery/{name}/{component}.png)",
                "",
                definition.description.strip(),
                "",
                "| Parameter | Default | Limits | |",
                "|---|---|---|---|",
            ]
            for parameter in definition.parameters:
                what = parameter.description or ""
                if parameter.internal:
                    what = f"*(derived)* {what}".strip()
                lines.append(
                    f"| `{parameter.name}` | `{cell(number(parameter.default))}` "
                    f"| {cell(limits(parameter))} | {cell(what)} |"
                )
            if definition.ports:
                lines += ["", "| Port | At | Direction | Width | |", "|---|---|---|---|---|"]
                for port in definition.ports:
                    lines.append(
                        f"| `{port.name}` | `{cell(port.at or 'origin')}` "
                        f"| {cell(_given(port.direction))} | {cell(_given(port.width))} "
                        f"| {cell(port.description)} |"
                    )
            lines += ["", f"*Tags: {', '.join(definition.tags)}*", ""]
    return "\n".join(lines).rstrip() + "\n"


def picture(component: str, path: Path) -> None:
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF

    from mems_sketch.engine import Engine

    process = PROCESS.get(component, "surface")
    project = Project(name="gallery", process_name=f"mems.{process}")
    project.use_process(f"mems.{process}")
    geometry = Engine().load(project).build(component).geometry
    x0, y0, x1, y1 = geometry.bbox()
    margin = 12
    scale = min((WIDTH - 2 * margin) / (x1 - x0), (HEIGHT - 2 * margin) / (y1 - y0))
    image = QImage(
        round((x1 - x0) * scale) + 2 * margin,
        round((y1 - y0) * scale) + 2 * margin,
        QImage.Format.Format_ARGB32,
    )
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    for layer in geometry.layer_names():
        colour = QColor(COLOURS.get(layer, "#888888"))
        fill = QColor(colour)
        fill.setAlpha(120)
        painter.setPen(QPen(colour, 1))
        painter.setBrush(fill)
        for hull, holes in geometry.polygons(layer):
            outline = QPainterPath()
            for ring in [hull, *(holes or [])]:
                outline.addPolygon(
                    QPolygonF(
                        [
                            QPointF(margin + (x - x0) * scale, margin + (y1 - y) * scale)
                            for x, y in ring
                        ]
                    )
                )
                outline.closeSubpath()
            painter.drawPath(outline)
    painter.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(path))


def main() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 - painting needs one
    for name in bundled_names():
        target = PAGES / f"library-{name}.md"
        target.write_text(page(name), encoding="utf-8")
        print(target.relative_to(ROOT))
        for component in bundled_library(name).components:
            path = IMAGES / name / f"{component}.png"
            picture(f"{name}.{component}", path)
            print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
