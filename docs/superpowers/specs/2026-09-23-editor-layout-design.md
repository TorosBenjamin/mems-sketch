# Editor layout

Redesign of the main window's chrome, built as the `MainWindow` split
(refactor part 3, first half; the second half, tools talking to an `Editor`
interface, is designed separately). The agreed mockup (v3) is at
https://claude.ai/artifact/7LwCVQHHWN7gzfoCstcHiv.

## Goal

- One toolbar row; no menu bar and no left tool palette.
- IntelliJ-style tool windows opened from stripes on the window edges.
- Canvas modes on the canvas; adding shapes and acting on the selection
  from menus, including a right-click menu in the editor.
- `MainWindow` composed from focused modules, so a panel or a command is
  added in one place.

## Layout

**Toolbar (one row):** ☰ main menu, project name, undo, redo, *Add ▾*
(primitives), *Place ▾* (components), *Operations ▾*; right: split editor,
find action, settings. New, open, save and export are in ☰ → File only.

**Tool windows**, opened and closed from the stripes:

| Anchor | Windows | Behaviour |
|---|---|---|
| left stripe, top group → `left-top` | Components | |
| left stripe, second group → `left-bottom` | Shapes, Layers | one at a time |
| left stripe, bottom → `bottom` | Messages | |
| right stripe → `right` | Properties, Parameters, Points | one at a time |

One window per anchor. `left-top` and `left-bottom` split the left side
when both are open. First start: Components, Shapes, Properties, Messages.
Open windows and sizes are app settings (not project state).

**Canvas:** mode palette in the top-right corner (Select V, Hand H, Move M,
Rotate R, Align A, Measure D) showing the active mode; zoom buttons bottom
right; caption `<component> · Drawn ▾` with the view mode (drawn, as
etched, etch compensated) per tab.

**Right mouse button:** a click (under 4 px of movement) opens the context
menu; a drag pans as before. Middle drag and Space+drag pan as before.

**Right-click menu:** *Add ▸* (rectangle, circle, arc, polygon, path) and
*Place component ▸*; with a selection: *Combine ▸* (union, subtract,
intersect, XOR), offset, fillet, transform, map layers, make component,
unpack component, *Rotate & mirror ▸*, duplicate, delete. Right-clicking an
unselected shape selects it first. Entries that do not apply are disabled.
Adding a drawn primitive from it starts the drawing tool with its first
point at the click.

**Drawing tools** (rectangle B, circle C, polygon P, path W) have no
buttons: they start from Add or their shortcuts. Arc inserts a default arc.

**Status bar:** tool hint; snap toggles; while drawing, draw layer and path
width; problem count (opens Messages); grid; zoom; coordinates. Replaces
the tool-options row.

**Process tab:** constants and layer definitions (GDS, undercut, rules),
opened from a *Process* item in the project tree or ☰ → View → Process.
At most one per window; remembered with the component tabs. The Layers
window keeps visibility and colour only.

**Rotate 90° and mirror:** right-click menu, ☰ → Edit, shortcuts; no buttons.

All shortcuts keep working with the menu bar gone (actions stay registered
on the window); find action still lists every command.

## Modules

| Module | Responsibility |
|---|---|
| `gui/actions.py` | Every command as a `QAction` and every menu built from them (☰ menus, Add, Place, Operations, right-click) |
| `gui/toolbar.py` | The toolbar row |
| `gui/toolwindows.py` | `ToolWindows`: stripes, panel areas, switching, persistence; `add(name, title, icon, widget, anchor)` |
| `gui/statusbar.py` | The status bar widgets |
| `gui/process_view.py` | The Process tab |
| `gui/canvas.py` | + mode palette, zoom buttons, caption with view mode, right-click menu |
| `gui/app.py` | `MainWindow`: composes the above, plus selection, hit-testing and running commands |

`QDockWidget`, the menu bar, the tool-options toolbar and the left palette
are removed.

## Steps

One commit each, suite green, app runnable:

1. `actions.py`: commands, menus and shortcuts move out of `MainWindow`
   unchanged.
2. `toolwindows.py` replaces the docks, with the anchors above.
3. Toolbar row and status bar; menu bar and tool-options row go.
4. Canvas: mode palette, zoom, caption view mode, right-click menu; left
   palette goes.
5. Process tab; Layers window reduced to visibility and colour.
6. New screenshot (rendered offscreen) and README.

## Testing

Existing GUI tests keep their behaviour checks; lines that find chrome by
its old home move to the new names. New tests: stripes open and close
windows, one per anchor, left split; panel state survives a restart;
right-click menu entries and enabled states; right-drag still pans; Add ▸
Rectangle from the right-click menu starts at the click; the mode palette
switches tools; the caption changes the tab's view mode; the Process tab
opens, edits a constant with undo and is remembered; every shortcut still
works without the menu bar. Each step is also checked on an offscreen
screenshot of the real app.

## Out of scope

- Tools talking to an `Editor` interface (rest of part 3).
- More right-panel windows (each is one `tool_windows.add` call later).
