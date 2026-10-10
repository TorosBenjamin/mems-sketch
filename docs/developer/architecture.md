# Architecture

```
  project folder (YAML)  ──►  backend: model + engine  ──►  geometry, rule checks, exports
  source of truth, in git      mems_sketch.core, _core         │
          ▲                          ▲                        ▼
          └──── edits ──── frontends: GUI (mems_sketch.gui), CLI, Python scripts
```

- **Source:** a project folder of YAML files ([file formats](file-formats.md)).
  It is small, readable, and gives meaningful git diffs.
- **Backend:** reads a project, resolves parameters, builds geometry, checks
  rules and exports. It never imports Qt; `tests/test_architecture.py`
  enforces that.
- **Frontends:** the GUI, the CLI and scripts all work through the same
  backend API and the same files.

Geometry is built by the C++ engine on the geometry library (Open CASCADE,
exact curves): see [Geometry core architecture](core-architecture.md). This
page describes the code as it is now.

## The model

- A `Project` (`core/project.py`) holds a `Process` (layers, constants), its
  components (`ComponentDef`: parameters, points and a shape tree), libraries
  (read-only sets of components from other folders) and imported layouts. It
  is plain data; building geometry is the engine's job.
- **Components are resolved by name** from the inside out: a component's own
  private components (`comb/finger`), its owner's, the shared ones, then the
  built-ins; `lib.name` names a library's. See the docstring of
  `core/project.py`.
- **Shapes** (`core/shapes/`) are pydantic models, one module per kind in
  `kinds/`. Kind-specific behaviour lives there only:
  `test_only_the_shape_kinds_switch_on_kinds` fails if other code switches on
  a shape's kind. Elsewhere, use the `Node` methods and class attributes
  (`placed`, `cuts`, `child_fields`, …).
- **Expressions** (`core/expressions.py`) are safe arithmetic over parameters,
  `process.*` and point coordinates (`beam.right.x`), with dependency
  resolution.

## The engine and its cache

Everything outside the backend builds through `mems_sketch.engine`: an
`Engine` is loaded with a project and makes `Build`s of its components (the
merged geometry, declared points, the nodes as evaluated). Behind it the C++
engine (`mems_sketch._core`, `src/engine/`) evaluates the shape trees on the
geometry library and hands back polygons on the 1 nm grid, which Python
holds as `Geometry`: a `Region` per layer (`core/region.py`), whose
booleans and offsets go back to the geometry library (`mems_sketch._geom`).
Polygons cross between C++ and Python as int64 NumPy arrays, never point by
point; the engine snaps each built region once and gives its box with it, and
a region caches its box and a hash of its points. Rule checks are cached by
that hash, and on a big design they run in the background
(`gui/views.py`): the edit is on screen at once, the violations follow.
Parameters and their checks stay with the Python model. Placements are plain
`Transform` values (`core/transform.py`), and code outside the backend uses
`Geometry`'s methods, never its regions.

- Every build is keyed by a **fingerprint** of everything it depends on: the
  definition, the components it places, parameter values, process constants,
  an imported file's digest. Identical placements are built once, and after an
  edit only the changed components and those containing them are rebuilt.
- The cache is in memory; the keys are designed so that it could be persisted
  later.
- Shapes are evaluated in the order their alignments need; a loop is an
  error. Modifiers run in the frame of the list holding the node, before its
  alignment moves it.

## Editing

`mems_sketch.editing` is how every frontend changes a project.

- An `EditSession` holds the project, the active component, undo/redo
  (whole-project snapshots, remembering which component each change was
  made in), trial values and the engine.
- **Command groups** do the changes: `session.components`, `.nodes`,
  `.modifiers`, `.corners`, `.moves`, `.points`, `.parameters`, `.process`,
  `.imports`, plus `.history` (git) and `.results` (what the components
  evaluate to).
- Every command runs as a **transaction** through `session.edit(description,
  change)`: the change is applied, the project is recompiled, and if a
  project that built before no longer does, it is rolled back exactly and the
  error is raised.
- `session.changed` and friends are plain callbacks (`editing/events.py`), so
  the backend stays free of Qt; the GUI connects to them.

## The GUI

`mems_sketch.gui` (PySide6). `app.py`'s `MainWindow` owns the session and
wires the parts; each part only talks to the session:

- `canvas.py`: a `QGraphicsView` in µm with y up; layers as cached path
  items; overlays (selection, violations, points, history changes).
- `tools.py`: canvas tools (Select, Move, Align, Corners, drawing tools…),
  each a small state machine fed with presses and moves in µm.
- `panels.py`, `points_panel.py`, `history_panel.py`, `properties.py`: tool
  windows; `toolwindows.py` places them around the editor.
- `actions.py`: every command with its shortcut, menus and toolbar.
- `settings.py`, `theme.py`, `icons.py`: preferences, the light and dark
  themes, and SVG icons coloured per theme.

## Package layout

| Module | Contents |
|---|---|
| `core/project.py` | `Project`, `Library`, `Instance`: components, name resolution, parameters |
| `engine.py` | `Engine` and `Build`: what everything outside the backend builds through |
| `core/region.py` | `Region`, `Box`: polygons on the 1 nm grid as int64 arrays; booleans and offsets through the geometry library |
| `core/transform.py` | `Transform`: placements as plain values |
| `core/shapes/` | The shape tree: one module per kind in `kinds/`, their registry, points, node records, modifiers, rewriting |
| `core/user_component.py` | `ComponentDef`, `ParamDef`, `PointDef` and their adapter to `Component` |
| `core/component.py` | `Component` base class, `Geometry`, the built-in components by name |
| `core/process.py` | `Process`, `Layer`, `Level` (the layer stack), process constants |
| `core/levels.py` | Components on levels: layers relative to a component's level (`level-1`, `level.anchor`) |
| `core/expressions.py` | Safe arithmetic expressions with dependency resolution |
| `core/imports.py` | Imported layouts (GDS cells) as components |
| `layout/` | Layout files without a layout library: GDSII read and written, OASIS and DXF written |
| `core/diff.py` | What changed between two versions of a project, in words and in geometry |
| `components/builtin/` | The built-in components as a library folder: `anchor`, `comb_drive`, `serpentine_spring` |
| `process/rules.py` | Design-rule checks: the project's rules checked by rule kinds, which are plugins (`mems_sketch.rules` entry points) |
| `process/rule_kinds.py` | The built-in rule kinds: widths, spacing, areas, pieces, enclosure, separation, anchored, release |
| `storage/` | Project folders (`project_files.py`: the manifest, processes, component folders), component files (`component_format.py`), one-file documents (`document.py`) in every format (`formats/`), git (`git.py`), the legacy SQLite importer |
| `export/` | Exporter plugins: GDSII, OASIS, DXF; geometry as JSON, XML, .mat |
| `editing/` | `EditSession`, the command groups, results and history |
| `cli.py` | `mems-sketch-cli` |
| `gui/` | The PySide6 editor |
