# Drawing and editing

## The canvas

- **Zoom** with the mouse wheel, a **pinch** on a touchpad, or Ctrl + scroll;
  **pan** with two fingers on a touchpad, the middle or right button, or Space +
  drag in any tool; **F** (or a two-finger double tap) fits the design. The
  buttons in the bottom-right corner zoom and fit too. *Preferences → Canvas →
  Scrolling* says what scrolling does if your device is taken for the other
  kind (a mouse wheel zooms, a touchpad pans).
- The **canvas modes** float in the top-right corner: Select, Hand, Move,
  Rotate, Align, Corners, Measure.
- The selection is outlined in orange; the shape under the cursor is outlined
  dashed (what a click would select); rule violations are boxed in red.
- The x axis is red and the y axis green; a scale bar sits in the corner, and
  the top left says what is shown. **View → Overlays** switches each of these
  on or off.
- **Right-click** (a click; a right drag pans) for **Add** (a primitive
  starting where you clicked), **Place component…** (placed where you clicked),
  and for the selection:
  combine, offset, fillet, transform, layer map, make or unpack component,
  rotate 90°, mirror, duplicate and delete.

## Tools

A tool stays active until another is chosen; **Esc** cancels what it is doing
and, pressed again, returns to Select. A shape added from **Add** (in
**Insert** or the right-click menu) is drawn once, then the editor returns to
Select; choose the drawing tool in the toolbar, or press its key, to draw
several.

| Tool | Key | What it does |
|---|---|---|
| **Select** | V | Click to select (Ctrl/Shift adds), drag the selection to move it, drag on empty space for a box: it selects what it touches (dashed), or what is entirely inside it (solid), as *Preferences → Editor → Box selection* says (also: by the direction you drag). A drag snaps a point of the moved shapes to another shape's point, else to the grid (Ctrl: no snapping); release with **Shift** on a snapped point to *align* there. The selected shape has **handles** to drag (below). |
| **Hand** | H | The left button pans (for trackpads). |
| **Move** | M | A gizmo: drag the red arrow along x, the green along y, the centre freely. Or click a base point, then where it goes. **Edit → Move by…** takes a typed dx, dy; arrow keys nudge by a grid step (Shift: a tenth). |
| **Rotate** | R | Drag anywhere to turn the selection about its centre; the angle follows the mouse (Ctrl: 15° steps). **Shift+click** sets another pivot (snaps to points), Esc goes back to the centre; a click without dragging selects. **Rotate 90°** is Ctrl+R / Ctrl+Shift+R; **Mirror** is in the right-click menu. |
| **Align** | A | Click a shape, one of its points, then the point to put it on (below). |
| **Corners** | O | Round or chamfer single corners (below). |
| **Measure** | D, N | Two modes, switched in the status bar (**Distance** / **Angle**): D measures a distance (two clicks: the length, dx and dy), N an angle (the vertex, then a point on each arm: two arms and an arc with the angle; Shift: 15° steps). Points snap to the shapes' points, then to the corners and edges of everything drawn, placed components included, then to the grid (Ctrl: no snapping); a dot shows where, ringed on a point and following the cursor along an edge. Rulers stay until **Tools → Clear rulers**. |
| **Rectangle**, **Circle** | B, C | Drag, or click corner and opposite corner (centre and radius). Shift: a square. |
| **Polygon**, **Path** | P, W | Click the points; double-click, Enter or (polygon) the first point finishes; Backspace takes back a point. Shift: 0°, 45°, 90° segments. |
| **Route** | E | A wire from port to port (the ports are marked): click a port, any via points, then another port. It runs along x and y between ports along x and y, else straight; as wide as its ports, or the path **Width** when they differ. With a route selected, drag its via points. See [Routes](shapes-and-operations.md#routes). |
| **Guide** | G | A construction line: draws nothing, but has points and can be mirrored about. |

The drawing tools draw on the layer chosen in the status bar (or by clicking a
layer in **Layers**); paths use the **Width** beside it. A drawn shape gets a
fresh name and is selected; its numbers can become expressions in Properties
afterwards.

**Moving keeps things parametric.** A move changes what the shape stores,
relative to its parent: `plate/2 + 39` moved by 11 becomes `plate/2 + 50`.
An aligned shape keeps its alignment and changes its offset, shown by the
cursor while you drag (Alt-drag removes the alignment instead). Primitives have no rotation of their own, so rotating
one wraps it in a transform.

**Drag a shape's handles to change it.** With the Select tool, one selected
shape shows its handles:

- a **rectangle** a square on each side and corner: a side moves one edge, a
  corner two;
- a **polygon** or **path** a dot on each vertex and a **+** in the middle of
  each side (drag it to add a vertex there), and a path a square for its width
  on the first segment's side;
- a **circle** its radius on the rim; an **arc** its outer and inner radius,
  and a dot on each end for its angles;
- a **guide** a dot on each end (a line shows where it goes while you drag);
- a **route** a dot on each via point (a line shows its new course, from port
  to port); Delete removes a chosen one.

Rounded corners made with the **Corners** tool follow the vertex they round
when a handle moves it, and go with a vertex that is removed (on a rectangle
or a polygon).

The cursor shows which way a handle drags. Handles snap as moving does, to
other shapes' points, else to the grid (Ctrl: no snapping); Shift keeps a
vertex or corner level, upright or at 45° from where it was. The shape is
previewed and the cursor shows the change (`x1  length → length + 3  (43 µm)`);
Esc drops the drag, and one Undo takes it back. Like moving, a drag **keeps
expressions**: it adds an offset. An **aligned** shape stays aligned: dragging
the side its alignment holds (its left, for one aligned by `left`) changes the
alignment's offset instead, so the other sides stay where they are.

**Hold Alt while dragging** to change the **parameter** instead: a side at
`x1 = length` dragged 3 µm sets `length` from 40 to 43, and every shape that
uses `length` (or is aligned to this one) follows; the cursor shows it
(`length  40 → 43   also moves: pad`). A parameter used through a derived one
(`reach = 2 * length + 1`) is found too, and stays within its limits (a whole
number for an integer one). A coordinate that names no parameter, or only one
with a trial value, gets an offset as without Alt.

**Parameters show on the selected shape.** With the Select tool, each of the
selected shape's values that uses a parameter (or a process constant) is drawn
as a dimension: `length = 40` along a beam, `w / 2 = 4` on a circle's radius,
`trace = 3` across a path. A coordinate is measured from the axis, so the
dimension says what the value is (`x1 = length` runs from x = 0 to the side).
Values that are plain numbers show nothing. **Point at a dimension's label** to
see what its parameter affects: every shape that uses it (directly or through
a derived parameter) and every shape aligned to one of those is outlined, and
the label by the cursor says which (`length  used by beam, rail · moves pad
(aligned)`).

**Click a dimension's label to change it.** Type a value and press Enter; the
shape is previewed meanwhile, with what else moves (`length  40 → 45   moves
beam, pad`). A value for `length` sets the parameter `length`; a number for
`w / 2` sets `w` so the radius is that number; an expression for `length`
becomes its default (`2 * w`), and one for `length + 3` replaces the value.
Type **`name = value`** to make a new parameter and use it there (`span =
60`). The same works for a handle you chose by clicking it: type `gap = 3` and
Enter, and that side, radius or width uses the new parameter `gap`. While you
type, letters go to the value, not to the tools' shortcuts; Esc or a click
elsewhere drops what you typed.

**Click a handle** (without dragging) to choose it; it is drawn filled.
**Delete** then removes a chosen vertex (a polygon keeps three, a path two)
instead of the shape. **Type a value** for the chosen handle, or while
dragging one, and press **Enter**: a side's coordinate (`45`), a vertex's or a
corner's x and y (`10, 30`), a radius, an angle in degrees, or a path's width.
Typed values keep expressions too (`length` typed 45 becomes `length + 5`).
Esc drops what you typed, and pressed again lets go of the handle.

**Cut, copy and paste** (Ctrl+X, Ctrl+C, Ctrl+V; in the Edit menu and the
right-click menus of the canvas and the Shapes tree) work on the selected
shapes. Paste puts the copies into the component in the current tab, where
the originals were, and selects them; names that are taken get fresh ones, and
pasted shapes that refer to each other's points still do. Parameters the
shapes use come with them when the component lacks them (with the same
defaults); where it has one by that name, the pasted shapes use it, and a
different default is pointed out. A component that cannot be placed there
(another component's private one) is refused. The clipboard holds the shapes
as text, so they can also be pasted into another window. Cut and paste are
one undo step each.

## Aligning parts

![Align: the candidates of the selected spring](../images/align-tool.png)

**Align** moves a shape so that one of its points lands on another shape's
point, plus an offset. It is a rule, not a one-off move: it is re-evaluated
after every change, so parts stay attached when parameters change.

1. Choose **Align** (A) and click the shape to move: its points are marked.
2. Click the point to align by, then the point to put it on.
3. Fine-tune the offset (e.g. 1 µm overlap) in **Properties**. **Edit →
   Remove alignment** takes it off and leaves the shape where it is.

While you pick, the point a click would take is named next to the cursor.

A selected aligned shape shows its alignment on the canvas: a ring on the
point it is kept on and a dot on its own point, named, with a dashed arrow
between them when an offset sets them apart.

In **Properties**, *Align to another shape's point* has a pick button beside
**Point** and beside **To**: it picks just that end on the canvas and keeps
the other end and the offset (Esc changes nothing). The **To** list groups
the points of each shape together.

In **Shapes**, a link icon marks an aligned shape; hover it for e.g. `bottom
at spring.end`.

## Rounding single corners

![The Corners tool on a pad: one corner rounded, one chamfered](../images/corners.png)

**Corners** (O) rounds or chamfers the corners you pick, each with its own
radius, where a fillet would round all of them:

1. Click a shape; its corners are marked (the small steps of curves are not
   corners).
2. Click a corner to round it with the last radius used; click it again to
   make it sharp. Drag away from a corner to set its radius: the rounding is
   drawn as you drag (the shape is rebuilt when you let go) and the radius
   stops at the largest that fits.
3. Each rounded corner has a handle on the line halfway between its edges, as
   far from the corner as its radius, with the radius beside it. Drag the
   handle to change the radius; a click on it changes nothing.
4. In **Properties**, the shape's **Corners** card lists each corner's radius
   and style (round or chamfer).

A corner whose radius no longer fits (the shape got smaller with build checks
switched off) is boxed on the canvas like a rule violation, and in the Corners
tool its label says it is too large.

A corner is recorded so that it follows the design: as the shape's own point
(`top_right`), a neighbour's point, or the expressions the shape is written
with (`slot_x` for a corner a cut made). It works on a placed library part
too: the rounding belongs to the placement, not to the library.

## Properties

The selected shape's fields. The name is the title (click to rename), with an
eye beside it that hides or shows the shape; the panel's header says what the
shape is in words (*Rectangle*, *Placed component*, *Subtract*). A placed
component's **Component** row names it once, with a pencil button that opens
it in its own tab; the button's arrow replaces it with another component.

The fields come in parts, each a heading over a line: *Geometry* (or
*Placement* for a placed component), *Parameters of …*, *Align to another
shape's point* and *Modifiers*. The chevron by a heading folds that part away;
it stays folded for that kind of shape, also next time. The fields of all the
parts line up, and a **?** sits by the name of the field it explains. Units in labels are lighter (*Finger width µm*). A hidden shape is not drawn,
exported or checked, but it keeps its points: shapes aligned to it, or using
its points, stay where they are.

- **Any number field takes a number or an expression.** An expression is
  tinted and shows its value; a red border means it cannot be evaluated.
  Typing offers the matching parameters, process constants and points.
- **A long expression** (`abs(width - rim_width)`) opens a wider editor over
  its field when you edit it, as wide as the panel, wrapping onto more lines
  (Shift+Enter opens it for any field). Enter applies, Esc keeps the old value.
- **Leaving a field applies it**, as Enter does: click another field, the
  canvas or another shape and the edit is kept. Esc in the wider editor is the
  way to drop an edit.
- **The layer** is picked from a list with each layer's colour: the layers
  relative to the component's level first, each saying where it lands
  (`level+1 · metal`), then the layers by name.
- **Drag a number** left or right to change it, as in Blender (Shift: finer,
  Ctrl: round steps; counts go in whole numbers). The canvas follows live and
  the change is one undo step. A click without dragging edits the text.
- For a placed component, its parameters are listed with their defaults as
  placeholders.
- **Modifiers** are cards (array, polar array, mirror, corners): switch one
  on or off, reorder, apply or remove it. See
  [modifiers](shapes-and-operations.md#modifiers).

## Shapes and Layers panels

**Shapes** lists the shapes of the component being edited, one line each: the
name, then what it is in grey (a primitive's layer, the component a reference
places). The eye hides a shape without deleting it; operations show
their operands under them. A placed component expands to show what is inside
it, read-only (changing it would change every copy); double-click such a row
to open the component with that shape selected.

**Layers** shows the layers with their colours and GDS numbers; the eye shows
or hides one, and clicking one makes it the drawing layer.
