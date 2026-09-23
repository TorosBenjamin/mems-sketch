# Editing in the backend

Sub-project 2 of 3 of the maintainability refactor (1: shape-kind registry,
done; 3: tools and `MainWindow`, designed separately afterwards).

## Goal

The editing logic of the GUI's `ProjectDocument` (1146 lines in
`gui/document.py`) becomes a Qt-free backend package, `mems_sketch/editing/`,
used directly by the GUI and usable from scripts and the CLI. The commands
are split into groups, one module each, so a new edit operation is one method
in one module.

## Constraints

- Behaviour does not change: the same transactions (apply, check, roll back),
  undo/redo, dirty tracking, trial values and inspection cache.
- No Qt in `mems_sketch/editing/` (enforced by `tests/test_architecture.py`).
- Existing tests change only where a call is renamed (table below).

## Layout

```
editing/
  __init__.py     EditSession, DragPlan, Event, new_project
  events.py       Event: connect, disconnect, emit
  session.py      EditSession
  results.py      Results: evaluated geometry and points, with their cache
  naming.py       fresh_name and helpers shared by the command groups
  commands.py     Commands: the base class of the groups (holds the session)
  components.py   ComponentEdits  -> session.components
  nodes.py        NodeEdits       -> session.nodes
  moves.py        MoveEdits       -> session.moves   (and DragPlan)
  points.py       PointEdits      -> session.points
  parameters.py   ParameterEdits  -> session.parameters
  process.py      ProcessEdits    -> session.process
```

`gui/document.py` and `ProjectDocument` go away; the GUI holds an
`EditSession` in the attributes it already calls `document`. `VIEW_MODES`
(display labels) moves to `gui/views.py`.

Group modules import `session` only under `TYPE_CHECKING`; `session.py`
imports the groups and creates them in `EditSession.__init__`.

## EditSession

Keeps: `project`, `active`, `path`, `dirty`, `compiler`, `trials`; the events;
`edit()`; `problems`; undo/redo (`can_undo`, `can_redo`, `undo_text`,
`redo_text`, `undo`, `redo`); files (`new`, `open`, `save`, `export`,
`modified`, and the classmethod `EditSession.open_project(path)` for scripts);
trial values (`set_trial`, `restore_trials`); lookups (`session`, `exists`,
`read_only`, `definition_of`, `shapes`, `active_definition`, `component_names`,
`component`, `parameter_defaults`, `node`, `unique_name`, `reference_target`);
`set_active`.

The method `session()` (the compiler session of the project) becomes
`compiled()`, so a group does not read `self.session.session()`. Private
helpers the groups use become public: `_local` → `local`,
`_shapes_in` → `shapes_in`, `_trials` → `trials_for`; `_siblings` becomes
`NodeEdits.siblings` (also used by `components.make`).

## Events

`Event` replaces the four Qt signals with the same names and the same
`connect` / `emit` calls. Slots run in connection order. An exception in a
slot is reported through `sys.excepthook` and the remaining slots still run,
as with PySide's direct connections, so a failing listener cannot undo a
committed edit.

## Call-site table

Every other `document.<name>` stays as it is.

| Before | After |
|---|---|
| `scope`, `inspection`, `geometry`, `check`, `node_regions`, `highlight`, `node_points`, `align_targets`, `declared_points`, `all_points`, `selection_center` | `results.<same name>` |
| `new_component` | `components.new` |
| `delete_component` | `components.delete` |
| `rename_component` | `components.rename` |
| `set_top` | `components.set_top` |
| `make_component` | `components.make` |
| `unpack` | `components.unpack` |
| `add_shape` | `nodes.add` |
| `add_primitive` | `nodes.add_primitive` |
| `add_component` | `nodes.add_component` |
| `replace_node` | `nodes.replace` |
| `remove_nodes` | `nodes.remove` |
| `duplicate` | `nodes.duplicate` |
| `set_enabled` | `nodes.set_enabled` |
| `set_align` | `nodes.set_align` |
| `wrap` | `nodes.wrap` |
| `unwrap` | `nodes.unwrap` |
| `drag_plan` | `moves.plan_drag` |
| `move` | `moves.move` |
| `rotate` | `moves.rotate` |
| `mirror` | `moves.mirror` |
| `transform_nodes` | `moves.transform` |
| `add_point` | `points.add` |
| `update_point` | `points.update` |
| `remove_point` | `points.remove` |
| `set_parameter` | `parameters.set` |
| `update_parameter` | `parameters.update` |
| `add_parameter` | `parameters.add` |
| `remove_parameter` | `parameters.remove` |
| `set_constant`, `rename_constant`, `remove_constant`, `add_constant`, `set_layer`, `add_layer`, `remove_layer` | `process.<same name>` |

The rewrite applies to `document.<name>` and `doc.<name>` (the tests' name
for it), with or without a call, since some are passed as slots.

## Steps

One commit each, full suite green:

1. Build `mems_sketch/editing/` from the code of `gui/document.py`;
   `tests/test_editing.py` is `tests/test_gui_document.py` with the calls
   rewritten and no Qt. The GUI still uses `gui/document.py`.
2. Rewrite the GUI and its tests with the table; delete `gui/document.py` and
   `tests/test_gui_document.py`; move `VIEW_MODES`.
3. Guard rails and docs: importing `mems_sketch.editing` loads no Qt; tests
   for `Event`; a script-level test (open the example, make a component,
   save, reopen); a README note on editing from scripts.

## Testing

The existing tests are the safety net and change only by the table. A missed
call site fails with `AttributeError`; after the rewrite, a grep for every
old name must come back empty.

## Out of scope

- Sub-project 3 (`MainWindow`, tools).
- New CLI commands (now possible; a separate decision).
