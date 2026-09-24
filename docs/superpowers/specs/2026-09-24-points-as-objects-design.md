# Points as objects

Points become things you work with, like shapes and parameters: named,
listed, found on the canvas. They are also the base for corner rounding
(a Corners modifier that records clicked corners), which comes after this.
Agreed with the user in conversation.

## The Points panel

A tree instead of a table, with a form under it for the selected point:

| Group | Rows | Editable |
|---|---|---|
| (top level) | The component's declared points | yes: name, at, x, y, description |
| Default | `center`, `left`, `top_right`, … of the whole component | no |
| One per named top-level shape | That shape's points (`beam.center`, …) | no |

- The declared points are the component's own; the default ones are what
  every component has (from its bounding box). Placers can use both. Shape
  points are what a declared point's `at` (and alignments) can measure from.
- **Hover** a row: that point is highlighted on the canvas, with its label.
- **Click** a row: the canvas pans to the point (the zoom stays) and the form
  shows it. The form edits a declared point (with value dragging); for the
  others it shows the reference and position, read-only.
- **Double-click** a declared point's name to rename it. Components that
  place this one follow: alignments (`to`, and `point` on the placing node),
  expressions (`comb1.anchor.x`), mirror `about` and their own points' `at`.
- **Right-click**: Copy reference, Rename, Delete; a default or shape point
  can be **re-exported**: it becomes a declared point measured from it, named
  like the original (`spring.end` → `end`, else `spring_end`), and the name
  is opened for editing. A placed part's group re-exports all its own points
  (not its box points) in one step.
- The form lives in the panel, not in Properties: Points and Properties
  share the right-hand anchor, so only one of them is open at a time.

## Points on the canvas only when you work with them

| When | Shown |
|---|---|
| The Points panel is open | Declared points with labels, the selected shape's points and its dashed box; the hovered or selected row's point, highlighted |
| Align tool picking | Its candidates (as before) |
| Otherwise | None |

A setting, View › Always show points (`canvas/always_show_points`, off),
keeps the old behaviour: declared and selected-shape points always shown.

## Not in this change

- Internal (non-exported) points: they have no use until corners are named;
  they come with the Corners modifier.
- The Corners modifier itself.

## Testing

Backend: default points match the bounding box; renaming a declared point
rewrites alignments, expressions and `at` in placing components, and the
undo restores them. GUI: the tree groups, hover and selection signals and
the pan, rename by editing, Name this point, the context menu, and which
markers the canvas shows with the panel open, closed and with the setting.
