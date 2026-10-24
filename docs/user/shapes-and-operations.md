# Shapes, operations and modifiers

A component's body is a **shape tree**, evaluated again whenever a value
changes. Operations are nodes in the tree, not edits that destroy their
input, so a subtraction stays editable and parametric.

## Kinds of shapes

| Kind | Shape | Notes |
|---|---|---|
| Primitive | `rect`, `polygon`, `circle`, `arc`, `path` | `rect` takes a `corner_radius` (clamped to half the shorter side: a fully rounded square is a circle, a rectangle a stadium); `arc` is an annular sector (a ring at 360°); `path` is a centreline with a width and flush, square or round ends |
| Reference | `ref` | A placed component, with its parameter values and position |
| | `connection` | The area that joins beam ends, made from their ports (see [Connections](#connections)) |
| | `route` | A wire from one port to another, which follows them (see [Routes](#routes)) |
| Operation | `transform` | Move, rotate, mirror and scale its shapes as one piece |
| | `boolean` | `a` subtract, intersect or XOR `b` |
| | `offset` | Grow (+) or shrink (−) outlines |
| | `fillet` | Round every convex (`radius`) and concave (`inner_radius`) corner |
| | `layer_map` | Move geometry between layers, e.g. derive an anchor layer from a device outline |
| Construction | `guide` | A line that draws nothing, but has points and can be aligned and mirrored about |

**Operations** (Operations ▾ or the right-click menu) wrap the selected shapes
in a new operation; **Edit → Unwrap** reverses it. There is no union: shapes
on one layer are merged anyway.

- **Booleans, offsets and fillets act per layer.** Subtracting a device-layer
  hole only affects the device layer. To combine layers, bring them onto one
  with `layer_map` first.
- **A shape can be in pieces.** A slot cut right through a beam leaves one
  shape in two pieces; the Shapes list says so ("2 pieces · subtract"). Its
  points come from the box around all the pieces, drawn dashed while it is
  selected.
- **Make component** (Ctrl+K) moves the selection into a new component; the
  parameters it uses become the new component's, so nothing moves. **Unpack
  component** (Ctrl+Shift+K) does the opposite.
- **Transform or component?** Make a component when something is reused or
  deserves its own parameters; use a transform to move or mirror a few shapes
  together once.

## Connections

A **connection** fills in where beams meet, with rounded corners, from the
beams' ends alone: select the beams (paths, or placed components with
ports) and choose **Operations → Connect**. For each beam it takes the port
facing the others; the beams are not changed.

```yaml
hub:
  kind: connection
  ends: [left.port.end, right.port.end, stem.port.end]
  inner_radius: 3   # the corners between the beams
  radius: 0         # the outer corners
```

- **Its outline:** taken around their centre, each neighbouring pair of
  beams' sides runs on until they meet, and the corner is rounded with
  `inner_radius` where it turns in between the beams or `radius` where it
  turns out (0: sharp). Where the sides meet behind an end, or never (two
  beams in line), the gap is closed straight. It reaches half a micrometre
  back into each beam, so it joins them without a seam.
- **Its ends** are ports (see [ports](components.md#ports)): a path has
  `start` and `end` at its ends, as wide as it; a placed component has the
  ports it declares. An array's copies have theirs by index
  (`row.port.drive[1]`).
- **It moves with its beams**, and follows when a beam or a port is renamed.
- **It is drawn** on its ends' layer, or `layer`; a port without a width
  takes the connection's `width`.

Each way it can be wrong is an error that names the end:

| Error | What it means |
|---|---|
| *is not on its shape* | Nothing of the beam is behind the port: the port is off the shape, or faces into it |
| *is not square across the end there* | The beam is not there all the way across the port: a rounded corner, a curve, or a port wider than the beam |
| *points away from the others* | That end faces away from the junction |
| *radius … does not fit between ends …: at most …* | The corner is too close to the ends for that radius; the largest that fits is given |
| *crosses itself* | Ends overlap, or face past one another |
| *different layers* | The ends are on different layers: give the connection a `layer` |
| *has no width* | Its port has no width: give the connection a `width` |

## Routes

A **route** is a wire from one port to another: move a part and its wires
follow. Draw one with the **Route** tool (E, or Add → Route): click a port
(they are marked), any via points, then another port. With a route selected,
the Route tool shows its via points: drag one to move it.

```yaml
drive_line:
  kind: route
  start: comb1.port.drive
  end: pad1.port.signal
  style: manhattan     # manhattan | l | straight
  via: [[50, 120]]     # points it passes through, in order
  bend: round          # sharp | round | chamfer
  radius: 5
  width: 4             # default: its ports'
```

- **Its style** says which way it goes. `manhattan` (the default) runs
  along x and y only: it leaves the start and comes into the end along the
  ports' directions, which must be along x or y, with as few bends as it can
  and then as short as it can (a Z bends halfway; ports facing the same way
  get a U; a port facing away gets a way round). `l` runs along the start's
  direction, then the end's, with one bend where the two lines meet.
  `straight` runs in straight lines from the start through each via point to
  the end, whatever way the ports face.
- **Its bends** are `sharp`, `round` (an exact arc of `radius`, which must be
  more than half the width) or `chamfer` (cut `radius` along each side, as a
  corners modifier's chamfer). A Manhattan route leaves room for them: bends
  at least a width (or two radii) apart and a width's half (or a radius) from
  a port; when an offset is too small for a Z with these bends, it goes round.
- **Its width and layer** are its ports' unless `width` or `layer` is
  given. Ports of different widths need a width (a taper waits for
  variable-width paths, SHP-10).
- **Its own ports** `start` and `end` are where it ends, pointing out of it,
  so a connection or another route can carry on from it.
- **It does not avoid other shapes.** It goes where its style and via points
  say; the connectivity check (#183) will report what it shorts.

| Error | What it means |
|---|---|
| *a manhattan route runs along x and y, and its start faces 30°* | A port is not along x or y: use `straight` or `l` |
| *an l route needs its ports' lines to meet* | The ports are parallel (and not facing on one line): use `manhattan` |
| *… where its ports' lines meet, and that is behind the start* | The lines meet behind a port |
| *bends of radius … do not fit between … : the largest that fits is …* | The bends are too large for the runs between them |
| *the route bends … from a port, nearer than half its width* | It would cut back across the port |
| *turns back on itself* | A via point sends it straight back |
| *different widths*, *different layers*, *have no width* | Give the route a `width` or a `layer` |

## Modifiers

Any shape can carry **modifiers**, a stack applied in order as in Blender.
They are cards in Properties (**Add modifier**).

| Modifier | Makes |
|---|---|
| **Array** | `columns` × `rows` copies, `dx`, `dy` apart (the step from one copy to the next). `i` and `j` are each copy's column and row, so copies can differ: `length: 40 + 2*i`. A new array has 3 copies, `dx: self.width * 2`: a copy's width apart |
| **Polar array** | `count` copies around a centre, `step` degrees apart, turned with the circle or keeping their orientation |
| **Mirror** | The shape and its mirror image across a vertical or horizontal line, a guide (`about: centerline`), or through a point (`about: mass.center`) |
| **Corners** | Chosen corners rounded or chamfered, each with its own radius (see [the Corners tool](drawing-and-editing.md#rounding-single-corners)) |
| **Perforate** | Holes on a square or hex grid, only where a whole hole fits (see [Perforation](#perforation)) |

- **The order matters:** mirroring an array is not arraying a mirror.
- **Values can refer to anything in reach**, including other shapes' points
  (`x: mass.center.x`) and the shape itself before the modifier (`self.left.x`
  mirrors a half across its own left edge). An array's spacing then reads
  `dx: self.width + 5` (5 µm gaps), `dx: self.width` (copies touching) or
  `dx: 20` (a fixed pitch).
- Modifiers work before the shape's alignment moves it, so a mirror about the
  mass's centre stays put when the part moves.
- **Apply** turns the first modifier into real shapes, as Blender's Apply does.

Example: two combs, mirrored about the plate's centre, three of each:

```yaml
- kind: ref
  name: comb
  component: mems.comb_drive
  modifiers:
  - {kind: mirror, axis: x, x: mass.center.x}
  - {kind: array, columns: 3, dx: 120}
```

## Perforation

**Perforate** fills any shape (a rectangle, a polygon, a boolean's result)
with release holes on a grid:

```yaml
plate:
  kind: polygon
  layer: device
  points: [[0, 0], [300, 0], [300, 200], [0, 200]]
  modifiers:
  - kind: perforate
    pitch: 20             # from one hole to the next
    grid: hex             # or square (the default); hex rows are pitch × √3/2 apart
    hole_size: 6          # hole_height too, for oblong holes
    corner_radius: 1      # 0: square holes; leave it out for round ones
    margin: 5             # holes stay this far inside the outline
    keep_out: [anchor]    # shapes the holes keep clear of (keep_out_margin, else margin)
```

- **Only whole holes:** a hole goes where it fits completely inside the
  outline shrunk by the margin, so there are no half holes at the edges.
- **The holes follow the shape:** resize or reshape it and they refill.
- **Centred:** the grid is centred on the shape, so the pattern stays
  symmetric; `x`, `y` put a hole's centre at a chosen place instead.
- **Webs and rim:** the webs between holes are `pitch − hole_size` wide; the
  rim is at least `margin`, and wider where the holes do not fill the shape
  exactly. For a rim as wide as the webs, tick **Even rim** (`even_rim:
  true`, square grid): the pitch then stretches a little along x and y so the
  holes fill the shape's box, every web and the rim the same width (`margin`,
  `x` and `y` are then not used). Each field's **?** in Properties explains it.
- **Hole shapes:** `hole_size` × `hole_height` with the corners rounded by
  `corner_radius`: round by default (half the size), square at 0, a rounded
  square in between, a slot when oblong.
- **Keep-out shapes** are named shapes of any layer: an anchor, or a
  `layer_map` bringing another layer's geometry in.
- Thousands of holes stay quick: one hole shape is placed at every site and
  cut out in one go. `mems.perforated_plate` is a rectangle with this modifier.

## Sizes and the minimal box

Every shape has the points of its bounding box (`left`, `top_right`,
`center`…) and its size, `width` and `height`: `beam.width` wherever points can
be used, `self.width` in a modifier. The box is tight on the exact geometry and
level in the shape's frame, so a polygon drawn at an angle gets a level box
whose corners sit off the shape.

The **minimal box** is the smallest rectangle around the shape at any angle,
taken on the exact outline (curves count). Its names start with `min.`:

| Name | Is |
|---|---|
| `beam.min.left`, `.min.top_right`, `.min.center`… | its points (`beam.min.top_right.x`) |
| `beam.min.width`, `beam.min.height` | its size, along its own sides |
| `beam.min.angle` | its angle from the x axis, between -45° and 45° |

A circle, a square or anything else whose smallest rectangle could lie at
several angles keeps its level box (angle 0), so the angle does not jump as
you edit. Align to a slanted beam's real corner with
`to: beam.min.top_right`, or turn a part with it: `rotation: beam.min.angle`.
The bounding box stays what points and alignments use unless you write `min.`.

## Units and precision

Units are micrometres. Coordinates snap to a 1 nm grid, and curves stay within
5 nm of the true arc.
