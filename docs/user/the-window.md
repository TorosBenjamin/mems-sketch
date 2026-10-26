# The window

The window follows JetBrains IDEs for the editor parts (one toolbar row, tool
windows opened from the edges, tabs, settings, Find Action) and
Blender/Unity for the geometry (canvas modes, gizmos, a red x axis and a green
y axis).

![The editor with the resonator example](../images/window.png)

## Welcome

Started without a project (or after **File → Close project**), the program
shows the Welcome window: **New project** (the wizard), **Open…** (a project
folder or any `.msketch` file) and the projects opened lately,
pinned ones first, then the most recent. Each shows its folder, when it was
opened and the git branch it is on. Type to search; Enter or a double-click
opens one in its own window. Point at a project for its pin; right-click to pin
it, show its folder or take it off the list. A project whose folder has gone is
marked missing: opening it asks where it is now.

## Toolbar

One row: **☰** (every menu is in it; menu paths in this guide such as
**View → Dark canvas** are inside it), the project's name, undo and redo,
then:

- **Add ▾**: primitives. Rectangle, circle, polygon and path start their
  drawing tool; an arc is added as a default one.
- **Place ▾** (Shift+A): components to put into the one you are editing (see
  [Placing components](#placing-components)).
- **Operations ▾**: subtract, intersect, XOR, offset, fillet, layer map,
  transform, make component.

On the right: split view, **Find Action** (Ctrl+Shift+A: type part of a
command's name, Enter runs it; Shift twice searches components, shapes and
parameters too) and settings.

**Right-click on the canvas** offers what fits where you clicked. On empty
space (which clears the selection) it is adding there: **Add**, **Place
component…** (it lands where you clicked), **Paste**, **Select all** (Ctrl+A) and **Fit**. On a shape it
is what can be done to the selection: the operations, **Rotate & mirror**,
**Zoom to selection** (Shift+F), **Rename…** (F2, for one shape: its name in
Properties, ready to type over) and cut, copy, paste, duplicate, delete.

In every menu a command's shortcut is in a lighter colour at the right, and
an option that can be switched on or off (**View → Dark canvas**) has a check
box.

Names from somewhere else are written with where they are from, as you type
them: `mems.perforated_plate` (a library's component), `base.top` (a shape's
point), `device.anchor` (a level's layer). The part before the dot is in a
lighter colour so the name stands out, in the Place popup, in the process
list and in the layer stack's levels.

### Placing components

**Place** (Shift+A) opens a popup listing the components you can put into
the one you are editing: the ones you placed last first, then this project's,
each library's (the bundled `mems` among them). Type to search (part of a
name, a library, the description or a tag; names that start with it come
first), Up/Down to
choose, Enter or a click to take it: the component follows the cursor, its
origin snapping to points and the grid (Ctrl: no snapping), until a click puts
it down. Esc lets it go. From a right-click on the canvas it lands where you
clicked instead.

**Browse all components** (or Tab) opens Search Everywhere on its
**Components** tab: each component with its description, under a heading per
source. Click a heading to fold that source away (it stays folded; a search
still shows what matches), or pick one source from the list at the top right.
Beside the list the chosen component is previewed: a picture of it at its
defaults, its description, parameters and points. Enter (or **Place**) places
it, Ctrl+Enter (or **Open**) opens it.

A component's description is the sentence or two you give it: with nothing
selected, Properties shows the open component's **Description**, a box that
grows with its text (the first sentence is what the popup shows). Leaving it
or Ctrl+Enter applies it; a blank line starts a new paragraph, and
`` `code` `` and `*emphasis*` are shown as such wherever the description is.

The toolbar is also the window's title bar: minimize, maximize and close are
at its right end. Drag an empty part of it to move the window, double-click it
to maximize or restore, and drag the window's edges or corners to resize it.
**Settings → Appearance → Use the toolbar as the title bar** switched off
brings the system's title bar back.

## Tool windows

Panels open and close from the stripes on the window's edges:

| Where | Windows |
|---|---|
| Left stripe, top | **Components**, **Commit** |
| Left stripe, below | **Shapes**, **Layers** |
| Left stripe, bottom | **Messages**, **Git** (along the bottom) |
| Right stripe | **Properties**, **Parameters**, **Points** |

Each place shows one window at a time; the left side splits when Components
and Shapes (or Layers) are both open. Every panel has a **?** in its header:
hover it for what the panel is for. The same **?** explains things in place
elsewhere (alignment, points, the process, each setting). Which windows are
open, and their sizes, are remembered. **View → Panels** lists them too. Drag
a panel's edge past the narrowest it can be and it closes; it opens again at
the size it had.

## Tabs and split view

Every component opens in its own tab, with its own zoom, selection and view;
the panels show the current tab. Double-click a component in **Components**,
or a placed component on the canvas or in **Shapes**, to open it.

**View → Split right** (Ctrl+\\) shows two tabs side by side: edit the spring
on one side and watch the resonator that uses it on the other.

![Split view: the resonator and its suspension](../images/split-view.png)

- A `*` on a tab marks a component changed since the last save; a star marks
  the top component, a lock a read-only one.
- Right-click a tab to close it, the others or all, or to open it in the
  other pane.
- Library components open **read-only** and show their
  interface, as a library does in code: the geometry, the description, the
  public parameters and the points. How they are built stays hidden
  (**View → Show implementation of read-only components** shows it).

## Status bar

On the left the active tool and its options (the drawing layer and path width
while drawing, the angle step for Rotate), then, dimmed, what to do now (left
out when the window is too narrow for it). Then the point the cursor would snap
to and the problems (click to open Messages). On the right, each in a field
that keeps its width: the snapping toggles (shape points, grid) and the gizmo
toggle, the grid step (or *snap 0.5 µm* with a fixed step), the zoom and the
cursor position in µm.

**Snapping is the editor's, not the design's.** By default drawing and moving
snap to the grid drawn, which gets finer as you zoom in. *Preferences →
Snapping → Grid step* fixes it instead (0.5 µm, say): the grid then draws only
lines on that step. Neither changes the geometry, which keeps 1 nm, nor an
export, which rounds to its own grid (the process's `grid_um`).

## Notes and motion

What a command did shows as a note at the bottom of the editor: "Exported
resonator.gds" (with **Show in folder**), "Aligned A to B", "Undid Move". A
note goes by itself after a few seconds (an error after longer); pointing at
it keeps it. A long note is cut to two lines: point at it for all of it, or
use **Show messages** on an error to see it in Messages. The status bar keeps
the hint for the tool you are using.

The canvas glides to a place rather than jumping: **Fit** (F), the zoom
buttons, and a problem chosen in Messages. Scrolling or panning takes over at
once. A shape you pick in the Shapes panel, in Search Everywhere or in the
Commit panel glows on the canvas for a moment, and comes into view if it was
outside.
Clicking a shape on the canvas selects it without the glow: you are already
looking at it.

While you drag or draw, the live value is next to the cursor as well as in
the status bar: a move's offset (and the point it snaps to), the angle of a
rotation, a rectangle's or circle's size, the length and angle of the polygon
or path edge you are drawing, a corner's radius. Pointing at a shape for a
moment says what it is: its name, kind and layer, or the component it places.
Over the selection the cursor shows that a drag moves it.

Scroll bars float over a panel's edge instead of taking a strip of it. A bar
shows while the content moves or the pointer is in the panel, widens when you
point at it, and fades out a moment later; while it is gone, clicks go to what
is under it.

**Settings → Appearance → Reduce motion** turns the animations off: the view
jumps, notes and scroll bars appear and go at once, and picked shapes do not
glow.

## Settings

**☰ → File → Settings…** (Ctrl+Alt+S): appearance (light, dark, as the
system or [your own theme](#your-own-theme); the canvas can keep its own
background), canvas (fill opacity,
outlines, grid, overlays, gizmo size, zoom step), snapping, editor defaults,
performance and the keymap: every command with a shortcut, its keys and the
menu it is in. The search field filters them, shortcuts too; changes apply at
once and are kept per user.

![Settings](../images/settings.png)

### Performance

*Settings → Performance* has defaults from your machine:

- **Threads for geometry:** how many processors one geometry operation may
  use (*Automatic*: all of them). Fewer leaves more for other programs.
- **Build cache:** how much memory built components may keep, so that what an
  edit does not change is not built again. *Automatic* is a quarter of the
  memory, 256 MB to 4 GB. Past the limit the least recently used go.
- **Build ahead:** while a whole number (a tooth count) is dragged in
  Parameters, the values next to it (1, 2 or 4 each way, or *Off*) are built
  in the background, so that moving on to one shows at once. They run only
  while processors are idle, other programs counted, at the lowest priority,
  and stop when the design changes for real. Nothing is built ahead of a
  component that took longer than *Only designs built in under* to build, and
  what is built ahead holds at most its *Share of the cache*, going first when
  the cache is full.

*Settings → Canvas → Performance* has **Check rules when a drag ends** (on): while
a value is dragged in Parameters, the design follows each step and Messages
says *checking*; the rules are checked once, when the mouse is released.

### Your own theme

A theme is a JSON file of colours. Put it in your themes folder (*Settings →
Appearance* shows where, with a button that opens it), named after the theme:
`ocean.json` is the theme `ocean`. It can start from another theme and change
only some colours:

```json
{
  "name": "Ocean",
  "parent": "dark",
  "ui": {"accent": "#1abc9c", "selected": "#1d4d47"},
  "canvas": {"background": "#0f1c24", "highlight": "#ffb347"},
  "icons": {"blue": "#1abc9c"}
}
```

- `ui` colours the window, `canvas` the layout canvas and `icons` the icons.
  `metrics` sets sizes in pixels: corner radii, header and row heights, the
  spacing scale and font sizes (for example `"metrics": {"row_height": 30}` for
  roomier tables). Radii and font sizes change at once; spacing and heights on
  the next start.
  The built-in [light](../../src/mems_sketch/gui/themes/light.json) and
  [dark](../../src/mems_sketch/gui/themes/dark.json) themes list every colour.
- Without a `parent`, a theme must give every colour.
- `dark` (`true` or `false`) says whether it is a dark theme; it is taken from
  the parent when left out. **View → Dark canvas** goes by it.
- Colours are `#rrggbb`, or `#aarrggbb` with transparency.
- The error, warning, info and done signs are drawn in `icons` `red`, `amber`,
  `blue` and `green`. `amber` is a yellow dark enough to read on the window's
  background; left out, the sign uses `yellow`.

The theme appears in *Interface theme* and *Canvas background* the next time
Settings opens. A theme that cannot be used (a misspelt colour name, a size
out of range, a parent that does not exist) is listed under the folder, with
the reason. A theme changes colours and sizes, never what is where.

## Undo and editor state

- **Undo/redo** is one history for the whole project and goes back to the tab
  where the change was made, reopening it if it was closed.
- Every edit is checked: if it would stop the project from building
  (including components that use the edited one), it is taken back with a
  message. On a design that takes a while to build, the edit shows at once
  and is checked when the build is done: then it is taken back (Redo shows
  it again).
- **How you were looking at the project** comes back when you reopen it: open
  tabs and the split, zoom and position per tab, selections, rulers,
  collapsed tree items, hidden layers, trial values, the drawing layer. It is
  kept in `.mems-sketch/` in the project folder, which is never committed to
  git; delete it to reset the views.
