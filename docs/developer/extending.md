# Extending

## A built-in component

Subclass `Component` (`core/component.py`), define a nested `Params` model and
`build(params) -> Geometry`, and decorate the class with
`@register_component`. Override `points()` to offer alignment points (as
`serpentine_spring` offers `start` and `end`). See `components/library.py`.

## A shape kind

Write a module in `core/shapes/kinds/` with a `Node` subclass (`Primitive` or
`Operation` for the usual defaults) that implements `render` and, as needed,
`moved`, `placement`, `summary`, `default` or `wrap`, and add the class to
`KINDS` in `kinds/__init__.py`.

`tests/test_shape_kinds.py` then checks it (YAML round trip, rendering,
moving). The Shapes list, Properties, every file format and the History
panel's change list pick it up without changes: they work from the model.

Kind-specific behaviour stays in the kind's module. Instead of `if shape.kind
== "boolean"` elsewhere, give `Node` a method or class attribute (like
`cuts` or `placed`) and override it in the kind.

## An edit command

A method on one of the command groups in `editing/` (`components.py`,
`nodes.py`, `moves.py`, …) that calls
`self.session.edit(description, change)`, where `change(project)` changes the
project in place. It gets undo, rollback on error and the description in the
Undo menu, and is usable from scripts at once.

To offer it in the editor, add an action in `gui/actions.py` and put it in a
menu, the toolbar or the right-click menu.

## A tool window

In `MainWindow._build_tool_windows` (`gui/app.py`):

```python
self.tool_windows.add(name, title, icon, widget, anchor, PANEL_HELP[name])
```

with an anchor of `left-top`, `left-bottom`, `bottom` or `right`, and the
panel's help text in `PANEL_HELP` (shown by its **?**). A panel talks only to
the session and emits `error(str)` for problems.

## An export format

A class with `format_name`, `file_extension` and
`export(project, geometry, path)`; set `wants_context = True` to also receive
`component=` and `params=`. Register it with `@register_exporter`, or from a
separate package under the `mems_sketch.exporters` entry-point group:

```toml
[project.entry-points."mems_sketch.exporters"]
svg = "my_package.svg:SvgExporter"
```

It then appears in **File → Export…** and `mems-sketch-cli export`.

## A file format for documents

A codec in `storage/formats/`: a module with `dump(tree) -> bytes`,
`load(bytes) -> tree` and a `CODEC = Codec(name, description, suffixes, dump,
load)`, listed in `formats.codecs()`. The codec must read back what it writes
for every tree (`test_every_codec_reads_back_what_it_wrote` checks this);
projects and geometry then work in the new format, including `convert`,
`load`, `save` and import. For geometry export, add a `DocumentExporter`
subclass in `export/document_formats.py`.
