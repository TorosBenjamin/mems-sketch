"""The icon set: small SVG drawings, coloured for the current theme.

Icons are drawn on a 16 × 16 grid with 1.2 px strokes, in the style of
JetBrains' New UI: a neutral outline colour plus one accent where it helps
(blue for geometry, purple for components, green/red for add/remove). The
colours come from the theme (see :mod:`mems_sketch.gui.theme`), so the same
drawing works on light and dark backgrounds.

``icon(name)`` returns a :class:`QIcon`; ``bind(action_or_widget, name)`` also
remembers the binding so :func:`set_theme` can recolour everything.
"""

from __future__ import annotations

import math
import weakref

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

COLORS = {
    "light": {
        "fg": "#6c707e",
        "blue": "#3574f0",
        "red": "#db3b4b",
        "green": "#208a3c",
        "orange": "#e66d17",
        "yellow": "#e5a50a",
        "purple": "#834df0",
        "x": "#e0443e",  # the x axis (Blender/Unity: red)
        "y": "#3f9b3f",  # the y axis (green)
    },
    "dark": {
        "fg": "#ced0d6",
        "blue": "#548af7",
        "red": "#e55765",
        "green": "#5fb865",
        "orange": "#e08855",
        "yellow": "#f2c55c",
        "purple": "#a571e6",
        "x": "#f0584f",
        "y": "#6cc36c",
    },
}
SIZES = (16, 20, 24, 32)


def _gear() -> str:
    points = []
    teeth = 8
    for i in range(teeth * 4):
        angle = 2 * math.pi * i / (teeth * 4)
        radius = 6.6 if i % 4 in (0, 1) else 4.9
        points.append(f"{8 + radius * math.cos(angle):.2f} {8 + radius * math.sin(angle):.2f}")
    return f'<path d="M{" L".join(points)} Z"/><circle cx="8" cy="8" r="2"/>'


def _arc_arrow(start: float, end: float, radius: float = 5.2) -> str:
    """An arc about the centre from ``start`` to ``end`` degrees (counter-clockwise
    when ``end > start``, y up) with an arrowhead at the end."""

    def at(angle, r=radius):
        a = math.radians(angle)
        return 8 + r * math.cos(a), 8 - r * math.sin(a)  # SVG y points down

    (x0, y0), (x1, y1) = at(start), at(end)
    ccw = end > start
    large = 1 if abs(end - start) > 180 else 0
    sweep = 0 if ccw else 1
    # the arrowhead: two strokes back from the tip, around the tangent
    tangent = math.radians(end + (90 if ccw else -90))
    tx, ty = math.cos(tangent), -math.sin(tangent)
    head = []
    for side in (1, -1):
        angle = math.atan2(ty, tx) + math.pi + side * math.radians(40)
        head.append(f"M{x1:.2f} {y1:.2f}l{3 * math.cos(angle):.2f} {3 * math.sin(angle):.2f}")
    return (
        f'<path d="M{x0:.2f} {y0:.2f}A{radius} {radius} 0 {large} {sweep} {x1:.2f} {y1:.2f}"/>'
        f'<path d="{"".join(head)}"/>'
    )


# The two circles of the boolean icons meet at x = 8, y = 8 ± 2√3.
_A = '<circle cx="6" cy="8" r="4"/>'
_B = '<circle cx="10" cy="8" r="4"/>'
_TOP, _BOT = "8 4.54", "8 11.46"
_CHIP = (
    '<rect x="4" y="4" width="8" height="8" rx="1" stroke="{purple}"/>'
    '<path d="M6 2v2M10 2v2M6 12v2M10 12v2M2 6h2M2 10h2M12 6h2M12 10h2"/>'
)

ICONS = {
    # -- tools --------------------------------------------------------------
    "select": '<path d="M4 1.8v10.6l2.9-2.7 1.9 4.3 1.9-.8-1.9-4.2h3.9z" fill="{fg}" stroke="none"/>',
    "hand": (
        '<path d="M5.5 8.5V3.6a1 1 0 0 1 2 0V7.5M7.5 7V2.6a1 1 0 0 1 2 0V7.5M9.5 7.3V3.8a1 1 0 '
        "0 1 2 0V9.8c0 2.6-1.7 4.4-4.1 4.4-1.7 0-2.6-.8-3.5-2.2L2.4 9.4a1 1 0 0 1 1.6-1.2L5.5 "
        '10"/>'
    ),
    "move": (
        '<path d="M8 1.8v12.4M1.8 8h12.4"/>'
        '<path d="M6.3 3.5 8 1.8l1.7 1.7M6.3 12.5 8 14.2l1.7-1.7M3.5 6.3 1.8 8l1.7 1.7M12.5 '
        '6.3 14.2 8l-1.7 1.7"/>'
    ),
    "rotate": _arc_arrow(-80, 150),
    "align": (
        '<rect x="1.8" y="2.3" width="6.2" height="5.7" rx=".6"/>'
        '<rect x="8" y="8" width="6.2" height="5.7" rx=".6"/>'
        '<circle cx="8" cy="8" r="1.7" fill="{blue}" stroke="none"/>'
    ),
    "measure": (
        '<path d="M1.9 10.6 10.6 1.9l3.5 3.5-8.7 8.7z"/>'
        '<path d="M4.4 8.1l1.4 1.4M6.4 6.1l1 1M8.4 4.1l1.4 1.4"/>'
    ),
    "angle": '<path d="M13.8 13.2H2.2l8.6-10.4"/><path d="M7.6 13.2a5.4 5.4 0 0 0-1.9-4.1"/>',
    "rect": '<rect x="2.4" y="3.6" width="11.2" height="8.8" rx=".6" fill="{blue}" '
    'fill-opacity=".25" stroke="{blue}"/>',
    "circle": '<circle cx="8" cy="8" r="5.6" fill="{blue}" fill-opacity=".25" stroke="{blue}"/>',
    "polygon": '<path d="M3.2 12.8 1.9 6.1 7.1 2l6.9 3.2-1.6 7.6z" fill="{blue}" '
    'fill-opacity=".25" stroke="{blue}"/>',
    "path": (
        '<path d="M2.6 13.4V8.6h5.2V4.2h5.8" stroke="{blue}" stroke-opacity=".35" '
        'stroke-width="3.2"/><path d="M2.6 13.4V8.6h5.2V4.2h5.8" stroke="{blue}"/>'
    ),
    "arc": '<path d="M1.8 11.5a6.2 6.2 0 0 1 12.4 0h-3a3.2 3.2 0 0 0-6.4 0z" fill="{blue}" '
    'fill-opacity=".25" stroke="{blue}"/>',
    # -- files and history ------------------------------------------------
    "new": '<path d="M3.5 1.8h5.8l3.2 3.2v9.2h-9z"/><path d="M9.2 1.8v3.3h3.3"/>'
    '<path d="M8 7.5v4.6M5.7 9.8h4.6" stroke="{green}"/>',
    "open": '<path d="M1.8 3.2h4.4l1.5 1.5h6.5v8.6H1.8z"/><path d="M1.8 6.8h12.4"/>',
    "save": '<path d="M2.4 2.4h8.8l2.4 2.4v8.8H2.4z"/><path d="M5 2.4v3.4h5.4V2.4"/>'
    '<rect x="4.6" y="9" width="6.8" height="4.6"/>',
    "undo": '<path d="M5.4 3.2 2.3 6.3l3.1 3.1"/><path d="M2.3 6.3h7.1a3.8 3.8 0 0 1 0 7.6H7"/>',
    "redo": '<path d="M10.6 3.2l3.1 3.1-3.1 3.1"/><path d="M13.7 6.3H6.6a3.8 3.8 0 0 0 0 7.6H9"/>',
    "export": '<path d="M8 10.2V1.9M5 4.8l3-2.9 3 2.9"/><path d="M2.4 9v4.7h11.2V9"/>',
    "import": '<path d="M8 1.9v8.3M5 7.3l3 2.9 3-2.9"/><path d="M2.4 9v4.7h11.2V9"/>',
    "recompile": '<path d="M13.2 6.2A5.4 5.4 0 0 0 3.1 5.3M2.8 9.8a5.4 5.4 0 0 0 10.1.9"/>'
    '<path d="M2.7 2.4v3.2h3.2M13.3 13.6v-3.2h-3.2"/>',
    "settings": _gear(),
    "search": '<circle cx="7" cy="7" r="4.6"/><path d="M10.4 10.4l3.8 3.8"/>',
    # -- operations -------------------------------------------------------
    "subtract": f'<path d="M{_TOP} A4 4 0 1 0 {_BOT} A4 4 0 0 1 {_TOP}z" fill="{{blue}}" '
    'fill-opacity=".3" stroke="{blue}"/>' + _B.replace("/>", ' stroke-dasharray="1.5 1.2"/>'),
    "intersect": f'<path d="M{_TOP} A4 4 0 0 1 {_BOT} A4 4 0 0 1 {_TOP}z" fill="{{blue}}" '
    'fill-opacity=".45" stroke="none"/>' + _A + _B,
    "xor": '<path fill-rule="evenodd" d="M2 8a4 4 0 1 0 8 0a4 4 0 1 0-8 0zM6 8a4 4 0 1 0 8 '
    '0a4 4 0 1 0-8 0z" fill="{blue}" fill-opacity=".3" stroke="{blue}"/>',
    "offset": '<rect x="5" y="5" width="6" height="6" fill="{blue}" fill-opacity=".3" '
    'stroke="{blue}"/><rect x="2" y="2" width="12" height="12" rx="2.4" '
    'stroke-dasharray="1.8 1.4"/>',
    "fillet": '<path d="M2.4 8V2.4H8" stroke-dasharray="1.4 1.2"/>'
    '<path d="M2.4 13.6V8.4a6 6 0 0 1 6-6h5.2" stroke="{blue}"/>',
    "transform": '<path d="M2.6 13.4V3.2M2.6 13.4h10.2" /><path d="M1.2 4.6l1.4-1.4 1.4 1.4M11.4 '
    '12l1.4 1.4-1.4 1.4"/><rect x="6.3" y="3.8" width="5.4" height="5.4" rx=".4" '
    'transform="rotate(20 9 6.5)" fill="{blue}" fill-opacity=".3" stroke="{blue}"/>',
    "layer_map": '<path d="M8 2.2 14 5.2 8 8.2 2 5.2z"/><path d="M2 8.4l6 3 6-3M2 11.4l6 3 6-3" '
    'stroke="{blue}"/>',
    "layers": '<path d="M8 2.2 14 5.2 8 8.2 2 5.2z"/><path d="M2 8.4l6 3 6-3M2 11.4l6 3 6-3"/>',
    "repeat": '<rect x="2" y="2" width="5" height="5" rx=".5"/><rect x="9" y="2" width="5" '
    'height="5" rx=".5"/><rect x="2" y="9" width="5" height="5" rx=".5"/><rect x="9" y="9" '
    'width="5" height="5" rx=".5"/>',
    # -- components -------------------------------------------------------
    "component": _CHIP,  # a project component: purple
    "component_library": _CHIP.replace("{purple}", "{blue}")  # a library component: blue
    + '<path d="M6.5 6.5h3v3h-3z" fill="{blue}" fill-opacity=".35" stroke="none"/>',
    "component_imported": _CHIP.replace("{purple}", "{green}")  # an imported cell: green
    + '<path d="M6.5 6.5h3v3h-3z" fill="{green}" fill-opacity=".35" stroke="none"/>',
    "component_builtin": _CHIP.replace("{purple}", "{orange}")  # a built-in: orange
    + '<path d="M6.5 6.5h3v3h-3z" fill="{orange}" fill-opacity=".35" stroke="none"/>',
    "guide": '<path d="M3 13 13 3" stroke="{blue}" stroke-dasharray="2 1.6"/>'
    '<circle cx="3" cy="13" r="1.4" fill="{blue}" stroke="none"/>'
    '<circle cx="13" cy="3" r="1.4" fill="{blue}" stroke="none"/>',
    "polar_array": '<circle cx="8" cy="8" r="5" stroke-dasharray="1.4 1.4"/>'
    '<rect x="6.5" y="1.5" width="3" height="3" rx=".5" fill="{blue}" fill-opacity=".35" '
    'stroke="{blue}"/><rect x="11.5" y="6.5" width="3" height="3" rx=".5" fill="{blue}" '
    'fill-opacity=".35" stroke="{blue}"/><rect x="6.5" y="11.5" width="3" height="3" rx=".5" '
    'fill="{blue}" fill-opacity=".35" stroke="{blue}"/><rect x="1.5" y="6.5" width="3" '
    'height="3" rx=".5" fill="{blue}" fill-opacity=".35" stroke="{blue}"/>',
    "parameter": '<path d="M8 2.2 13.8 8 8 13.8 2.2 8z" stroke="{purple}"/>'
    '<path d="M8 5.6 10.4 8 8 10.4 5.6 8z" fill="{purple}" stroke="none"/>',
    "up": '<path d="M4 10l4-4 4 4"/>',
    "down": '<path d="M4 6l4 4 4-4"/>',
    "apply": '<path d="M3.4 8.4l3 3 6.2-6.6" stroke="{green}"/>',
    "eye_off": '<path d="M1.6 8s2.4-4.4 6.4-4.4S14.4 8 14.4 8 12 12.4 8 12.4 1.6 8 1.6 8z"/>'
    '<circle cx="8" cy="8" r="2"/><path d="M2.6 13.4 13.4 2.6"/>',
    "modifier": '<path d="M9.6 2.2a3.4 3.4 0 0 0-3.2 4.6l-4.2 4.2a1.3 1.3 0 0 0 1.9 1.9'
    'l4.2-4.2a3.4 3.4 0 0 0 4.6-3.2l-2 1.2-1.9-1.1V3.4z" stroke="{blue}"/>',
    "link": '<path d="M6.6 9.4l2.8-2.8"/><path d="M7.4 4.6l1.3-1.3a2.6 2.6 0 0 1 3.7 3.7'
    'l-1.3 1.3M8.6 11.4l-1.3 1.3a2.6 2.6 0 0 1-3.7-3.7l1.3-1.3" stroke="{blue}"/>',
    "collapse": '<path d="M4.5 3.5 8 6.5l3.5-3M4.5 12.5 8 9.5l3.5 3"/>',
    "make_component": _CHIP.replace(
        'stroke="{purple}"', 'stroke="{purple}" stroke-dasharray="1.6 1.2"'
    )
    + '<path d="M8 6v4M6 8h4" stroke="{green}"/>',
    "unpack": _CHIP.replace('stroke="{purple}"', 'stroke="{purple}" stroke-dasharray="1.6 1.2"')
    + '<path d="M6.5 6.5l3 3M9.5 6.5l-3 3" stroke="{orange}"/>',
    "place": _CHIP.replace('rx="1"', 'rx="1" fill="{purple}" fill-opacity=".2"')
    + '<path d="M8 5.8v4.2M6.3 8.4 8 10.1l1.7-1.7" stroke="{purple}"/>',
    "library": '<path d="M2.5 2.5h4v11h-4zM6.5 2.5h3.2v11H6.5z"/>'
    '<path d="M10 3.2l3-.8 2.2 10.8-3 .8z" stroke="{blue}"/>',
    "builtin": '<path d="M8 1.8 13.6 5v6L8 14.2 2.4 11V5z"/><path d="M2.4 5 8 8.2 13.6 5M8 8.2v6" '
    'stroke="{orange}"/>',
    "top": '<path d="M8 1.8l1.9 3.9 4.3.6-3.1 3 .7 4.3L8 11.6l-3.8 2 .7-4.3-3.1-3 4.3-.6z" '
    'fill="{yellow}" fill-opacity=".35" stroke="{yellow}"/>',
    "lock": '<rect x="3.2" y="7" width="9.6" height="7" rx="1"/><path d="M5.2 7V5a2.8 2.8 0 0 1 '
    '5.6 0v2"/>',
    "edit": '<path d="M2.4 13.6l.6-3.2 7.9-7.9 2.6 2.6-7.9 7.9z"/><path d="M9.6 3.8l2.6 2.6" '
    'stroke="{blue}"/>',
    "folder": '<path d="M1.8 3.2h4.4l1.5 1.5h6.5v8.6H1.8z"/>',
    # -- editing ----------------------------------------------------------
    "add": '<path d="M8 3v10M3 8h10" stroke="{green}"/>',
    "remove": '<path d="M3 8h10" stroke="{red}"/>',
    "delete": '<path d="M2.6 4h10.8M6.2 4V2.4h3.6V4M4 4l.7 9.6h6.6L12 4"/><path d="M6.6 6.6v4.6M9.4 '
    '6.6v4.6"/>',
    "duplicate": '<rect x="5.2" y="5.2" width="8.4" height="8.4" rx="1"/><path d="M10.8 5.2V2.4H2.4'
    'v8.4h2.8"/>',
    "rotate_left": _arc_arrow(-80, 150) + '<text x="8" y="10" font-size="5" fill="{fg}" '
    'stroke="none" text-anchor="middle" font-family="sans-serif">90</text>',
    "rotate_right": _arc_arrow(260, 30) + '<text x="8" y="10" font-size="5" fill="{fg}" '
    'stroke="none" text-anchor="middle" font-family="sans-serif">90</text>',
    "mirror_h": '<path d="M8 1.6v12.8" stroke-dasharray="1.6 1.2"/><path d="M6 4 2 12h4z"/>'
    '<path d="M10 4l4 8h-4z" fill="{blue}" fill-opacity=".3" stroke="{blue}"/>',
    "mirror_v": '<path d="M1.6 8h12.8" stroke-dasharray="1.6 1.2"/><path d="M4 6 12 2v4z"/>'
    '<path d="M4 10l8 4v-4z" fill="{blue}" fill-opacity=".3" stroke="{blue}"/>',
    "fit": '<path d="M2 5.4V2h3.4M10.6 2H14v3.4M14 10.6V14h-3.4M5.4 14H2v-3.4"/>'
    '<rect x="5.2" y="5.2" width="5.6" height="5.6" rx=".5" stroke="{blue}"/>',
    "zoom_in": '<circle cx="7" cy="7" r="4.6"/><path d="M10.4 10.4l3.8 3.8M7 4.9v4.2M4.9 7h4.2"/>',
    "zoom_out": '<circle cx="7" cy="7" r="4.6"/><path d="M10.4 10.4l3.8 3.8M4.9 7h4.2"/>',
    "split": '<rect x="1.8" y="2.4" width="12.4" height="11.2" rx="1"/><path d="M8 2.4v11.2"/>',
    "close": '<path d="M4 4l8 8M12 4l-8 8"/>',
    "eye": '<path d="M1.6 8s2.4-4.4 6.4-4.4S14.4 8 14.4 8 12 12.4 8 12.4 1.6 8 1.6 8z"/>'
    '<circle cx="8" cy="8" r="2"/>',
    "clear": '<path d="M2.6 13.4h10.8"/><path d="M4.6 11.4 10.8 3.2l2.4 1.8-5.4 6.4z"/>',
    # -- snapping and view -------------------------------------------------
    "snap": '<path d="M3.4 2.4v5.2a4.6 4.6 0 0 0 9.2 0V2.4H9.6v5.2a1.6 1.6 0 0 1-3.2 0V2.4z"/>'
    '<path d="M3.4 4.6h3M9.6 4.6h3" stroke="{red}"/>',
    "snap_points": '<circle cx="8" cy="8" r="3.6"/><path d="M8 1.6v3M8 11.4v3M1.6 8h3M11.4 8h3"/>'
    '<circle cx="8" cy="8" r="1.1" fill="{blue}" stroke="none"/>',
    "grid": '<rect x="1.8" y="1.8" width="12.4" height="12.4" rx="1"/>'
    '<path d="M1.8 6h12.4M1.8 10h12.4M6 1.8v12.4M10 1.8v12.4"/>',
    "axes": '<path d="M3 13V3.2" stroke="{y}"/><path d="M3 13h9.8" stroke="{x}"/>'
    '<path d="M1.6 4.6 3 3.2l1.4 1.4" stroke="{y}"/><path d="M11.4 11.6l1.4 1.4-1.4 1.4" '
    'stroke="{x}"/>',
    "point": '<circle cx="8" cy="8" r="4.2"/><path d="M8 2v12M2 8h12"/>',
    "ruler": '<path d="M1.9 10.6 10.6 1.9l3.5 3.5-8.7 8.7z"/>',
    # -- tool windows -----------------------------------------------------
    "minimize": '<path d="M4 8.5h8"/>',
    "help": '<circle cx="8" cy="8" r="6"/><path d="M6.2 6.3a1.9 1.9 0 1 1 2.6 1.8c-.5.2-.8.6-.8 1.1'
    'v.5M8 11.4v.3" stroke-width="1.4"/>',
    "menu": '<path d="M2.5 4h11M2.5 8h11M2.5 12h11"/>',
    "shapes": '<rect x="2" y="2" width="4" height="3" rx=".6"/><path d="M4 5v7.5h3M4 8.5h3"/>'
    '<rect x="8" y="7" width="6" height="3" rx=".6" stroke="{blue}"/>'
    '<rect x="8" y="11" width="6" height="3" rx=".6" stroke="{blue}"/>',
    "parameters": '<path d="M2.5 4.5h2M7.5 4.5h6M2.5 8h6.5M12 8h1.5M2.5 11.5h1M6.5 11.5h7"/>'
    '<circle cx="6" cy="4.5" r="1.5"/><circle cx="10.5" cy="8" r="1.5"/>'
    '<circle cx="5" cy="11.5" r="1.5"/>',
    "messages": '<path d="M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z"/><path d="M5.5 6.2h5M5.5 8.2h3"/>',
    "history": '<circle cx="8" cy="8" r="5.8"/><path d="M8 4.6V8l2.4 1.7"/>',
    "modified": '<circle cx="8" cy="8" r="3.2" fill="{orange}" stroke="none"/>',
    "properties": '<rect x="2.5" y="2.5" width="11" height="11" rx="1.5"/>'
    '<path d="M5 6h2M5 10h2M9 6h2M9 10h2"/>',
    # -- messages ---------------------------------------------------------
    "error": '<circle cx="8" cy="8" r="6.2" fill="{red}" stroke="none"/>'
    '<path d="M8 4.6v4.2M8 11.1v.3" stroke="#fff" stroke-width="1.6"/>',
    "warning": '<path d="M8 1.8 14.6 13.8H1.4z" fill="{yellow}" stroke="none"/>'
    '<path d="M8 6v4M8 11.7v.3" stroke="#222" stroke-width="1.5"/>',
    "info": '<circle cx="8" cy="8" r="6.2" fill="{blue}" stroke="none"/>'
    '<path d="M8 7.2v4.4M8 4.7v.3" stroke="#fff" stroke-width="1.6"/>',
    "ok": '<circle cx="8" cy="8" r="6.2" fill="{green}" stroke="none"/>'
    '<path d="M5 8.2l2 2 4-4.2" stroke="#fff" stroke-width="1.6"/>',
}

_theme = "light"
_cache: dict[tuple[str, str, str | None], QIcon] = {}
_bound: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def svg(name: str, color: str | None = None) -> str:
    """The SVG source of an icon, coloured for the current theme."""
    colors = dict(COLORS[_theme])
    if color is not None:
        colors["fg"] = COLORS[_theme].get(color, color)
    body = ICONS[name]
    for key, value in colors.items():
        body = body.replace("{" + key + "}", value)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" '
        f'stroke="{colors["fg"]}" stroke-width="1.2" stroke-linecap="round" '
        f'stroke-linejoin="round">{body}</svg>'
    )


def pixmap(name: str, size: int, color: str | None = None, ratio: float = 2.0) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(svg(name, color).encode()))
    image = QPixmap(round(size * ratio), round(size * ratio))
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size * ratio, size * ratio))
    painter.end()
    image.setDevicePixelRatio(ratio)
    return image


def icon(name: str, color: str | None = None) -> QIcon:
    """The icon ``name`` in the current theme's colours (``color`` replaces the outline)."""
    key = (_theme, name, color)
    if key not in _cache:
        result = QIcon()
        for size in SIZES:
            result.addPixmap(pixmap(name, size, color))
        _cache[key] = result
    return _cache[key]


def bind(target, name: str, color: str | None = None):
    """Give an action or widget the icon, and recolour it when the theme changes."""
    target.setIcon(icon(name, color))
    _bound[target] = (name, color)
    return target


def set_theme(theme: str) -> None:
    """Recolour every bound icon for the ``light`` or ``dark`` theme."""
    global _theme
    _theme = theme if theme in COLORS else "light"
    for target, (name, color) in list(_bound.items()):
        try:
            target.setIcon(icon(name, color))
        except RuntimeError:  # the Qt object is gone
            pass


def current_theme() -> str:
    return _theme


def color(key: str) -> str:
    """A named colour of the current theme (``fg``, ``blue``, ``x``, ...)."""
    return COLORS[_theme][key]
