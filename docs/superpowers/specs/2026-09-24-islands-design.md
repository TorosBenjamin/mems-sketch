# Islands look

The main window in the style of JetBrains' "Islands" theme: panels and the
editor as rounded islands on a frame. Approved from the user's reference
screenshot of IntelliJ (dark Islands).

## Measured from the reference (dark)

| Part | Colour / size |
|---|---|
| Frame: toolbar, stripes, gaps, status bar | `#26282c` |
| Islands: every tool window and the editor | `#191a1c` |
| Gap between islands | 5 px, no lines |
| Island corners | radius ≈ 10 px |
| Headers | bold title on the island colour, no separator |

## Design

- Theme tokens `frame` and `island` in `gui/theme.py`: dark `#26282c` /
  `#191a1c` (the reference); light `#ebecf0` / `#ffffff` (no reference; the
  same idea on a light frame). Toolbar, stripes, status bar, splitter gaps
  and the main window use `frame`; tool windows, their headers, trees,
  tables, the property editor and the editor area use `island`. No border
  lines between them.
- `ToolWindows`: every anchor's panel is an island (object name `island`,
  radius 10 px, 4 px inset so children never cover the corners). The bottom
  panel spans the full width between the stripes: a vertical splitter of
  [the row of left side, editor, right side] over [bottom]. Gaps are 5 px
  splitter handles in the frame colour; a 5 px frame margin below the
  islands. The two left anchors are two stacked islands.
- The editor area is one island; its tabs sit inside it. The selected tab
  is a rounded pill (no accent underline). The canvas keeps square corners,
  inset inside the island (clipping a live QGraphicsView to rounded corners
  is not practical).
- The active stripe button is a rounded pill; the status bar has no top
  line.
- The saved splitter state keeps its keys: `main` [left, editor, right],
  `left` [left top, left bottom], `center` [top row, bottom].

## Testing

- The bottom panel spans from the left side panel's left edge to the right
  side panel's right edge.
- A grab of the window shows the frame colour in a gap and the island
  colour inside a panel, in both themes, and switching theme changes both.
- The existing suite passes; screenshots of both themes compared with the
  reference.
