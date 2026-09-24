# Shapes, operations and modifiers

A component's body is a **shape tree**, evaluated again whenever a value
changes. Operations are nodes in the tree, not edits that destroy their
input, so a subtraction stays editable and parametric.

## Kinds of shapes

| Kind | Shape | Notes |
|---|---|---|
| Primitive | `rect`, `polygon`, `circle`, `arc`, `path` | `arc` is an annular sector (a ring at 360°); `path` is a centreline with a width and flush, square or round ends |
| Reference | `ref` | A placed component, with its parameter values and position |
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

## Modifiers

Any shape can carry **modifiers**, a stack applied in order as in Blender.
They are cards in Properties (**Add modifier**).

| Modifier | Makes |
|---|---|
| **Array** | `columns` × `rows` copies, `dx`, `dy` apart. `i` and `j` are each copy's column and row, so copies can differ: `length: 40 + 2*i` |
| **Polar array** | `count` copies around a centre, `step` degrees apart, turned with the circle or keeping their orientation |
| **Mirror** | The shape and its mirror image across a vertical or horizontal line, a guide (`about: centerline`), or through a point (`about: mass.center`) |
| **Corners** | Chosen corners rounded or chamfered, each with its own radius (see [the Corners tool](drawing-and-editing.md#rounding-single-corners)) |

- **The order matters:** mirroring an array is not arraying a mirror.
- **Values can refer to anything in reach**, including other shapes' points
  (`x: mass.center.x`) and the shape itself before the modifier (`self.left.x`
  mirrors a half across its own left edge).
- Modifiers work before the shape's alignment moves it, so a mirror about the
  mass's centre stays put when the part moves.
- **Apply** turns the first modifier into real shapes, as Blender's Apply does.

Example: two combs, mirrored about the plate's centre, three of each:

```yaml
- kind: ref
  name: comb
  component: comb_drive
  modifiers:
  - {kind: mirror, axis: x, x: mass.center.x}
  - {kind: array, columns: 3, dx: 120}
```

## Units and precision

Units are micrometres. Coordinates snap to a 1 nm grid, and curves stay within
5 nm of the true arc.
