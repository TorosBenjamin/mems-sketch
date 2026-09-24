# Architecture

```
  project folder (YAML)  ──►  backend = compiler  ──►  geometry, rule checks, exports
  source of truth, in git      mems_sketch.core …            │
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

## The model

- A `Project` (`core/project.py`) holds a `Process` (layers, constants), its
  components (`ComponentDef`: parameters, points and a shape tree), libraries
  (read-only sets of components from other folders) and imported layouts. It
  is plain data; building geometry is the compiler's job.
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

## The compiler and its cache

`core/compiler.py`: a `Compiler` builds components into `Geometry` (KLayout
regions per layer, 1 nm database unit).

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
  made in), trial values and the compiler.
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
| `core/compiler.py` | `Compiler` and `Session`: fingerprints, cache, rendering |
| `core/shapes/` | The shape tree: one module per kind in `kinds/`, their registry, points, evaluation, modifiers, rewriting |
| `core/user_component.py` | `ComponentDef`, `ParamDef`, `PointDef` and their adapter to `Component` |
| `core/component.py` | `Component` base class, `Geometry`, the built-in component registry |
| `core/process.py` | `Process`, `Layer`, process constants |
| `core/expressions.py` | Safe arithmetic expressions with dependency resolution |
| `core/imports.py` | Imported layouts (GDS/OASIS cells) as components |
| `core/diff.py` | What changed between two versions of a project, in words and in geometry |
| `components/library.py` | Built-ins: `rectangle`, `anchor`, `comb_drive`, `serpentine_spring` |
| `process/rules.py` | Design-rule checks (minimum width and spacing) |
| `storage/` | Project folders (`project_files.py`), one-file documents (`document.py`) in every format (`formats/`), git (`git.py`), the legacy SQLite importer |
| `export/` | Exporter plugins: GDSII, OASIS, DXF; geometry as JSON, XML, .mat |
| `editing/` | `EditSession`, the command groups, results and history |
| `cli.py` | `mems-sketch-cli` |
| `gui/` | The PySide6 editor |
