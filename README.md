# mems-sketch

Parametric MEMS layout design in Python. Designs are built from parametric
components whose parameters can be expressions over shared variables. The tool
applies etch-loss compensation, checks design rules, and exports through
pluggable format modules. Simulation is out of scope.

## Status

Core library only. The GUI (PySide6) will be built on the same API, so
everything the GUI does can also be scripted.

## Install

```bash
pip install -e ".[dev]"
pytest
```

## Code-first use

```python
from mems_sketch import Design, Instance, Layer, export, save
from mems_sketch.process import etch, rules

design = Design(name="demo")
design.add_layer(Layer("device", 1, 0, undercut=0.3, min_width=1.5, min_space=1.5))
design.set_variable("w", 2.0)
design.add_instance(Instance("comb", "comb_drive", {"finger_width": "w", "gap": "w * 1.5"}))

print(rules.check(design))
save(design, "demo.mems")                                       # SQLite design file
export(design, "demo.gds", geometry=etch.compensated(design))   # format from extension
```

See `examples/comb_actuator.py` for a complete script.

## Layout

| Package | Contents |
|---|---|
| `core/component.py` | `Component` base class, `Params` (pydantic), `Geometry`, component registry |
| `core/design.py` | `Design`, `Layer`, `Instance`; resolves expressions and renders geometry |
| `core/user_component.py` | User-defined components: parametric polygons, rects and references |
| `core/expressions.py` | Safe arithmetic expression evaluator with dependency resolution |
| `components/library.py` | Built-in components: `rectangle`, `anchor`, `comb_drive`, `serpentine_spring` |
| `process/etch.py` | Lateral etch loss: `etched()` predicts, `compensated()` pre-biases |
| `process/rules.py` | Minimum width and spacing checks per layer |
| `storage/sqlite_store.py` | `.mems` design files (SQLite; parametric model only, no geometry) |
| `export/` | Exporter plugins: GDSII, OASIS, DXF |

Units are micrometres; the database unit is 1 nm.

## User-defined components

Besides the built-in library, users can define their own components from
parametric primitives. Definitions are plain data, saved in the `.mems` file
and editable from the GUI:

```python
from mems_sketch import ComponentDef, ParamDef, RectShape, PolygonShape, RefShape, Repeat

fingers = ComponentDef(
    name="finger_array",
    parameters=[
        ParamDef(name="n", default=4, min=1, integer=True),
        ParamDef(name="w", default=2, min=0.5),
        ParamDef(name="pitch", default=6),
        ParamDef(name="taper", default=0),
    ],
    shapes=[
        # `i` and `j` are the column/row index inside a repeated shape
        RectShape(layer="device", x0=0, y0=0, x1="w", y1="30 + i * taper",
                  repeat=Repeat(columns="n", dx="pitch")),
        RefShape(component="anchor", params={"size": 20}, x="n * pitch / 2", y=-15),
    ],
)
design.define_component(fingers)
design.add_instance(Instance("f1", "finger_array", {"n": 12, "w": "w_global"}))
```

Shapes are `polygon`, `rect` and `ref` (a built-in or user-defined component,
so definitions can be nested). Any coordinate can be an expression over the
component's own parameters, and parameters can have limits and be integers.
Unknown references, circular references and invalid defaults are rejected
when the component is defined.

## Extending

**New built-in component:** subclass `Component`, define a nested `Params` model and
`build()`, and decorate the class with `@register_component`.

**New export format:** write a class with `format_name`, `file_extension` and
`export(design, geometry, path)`. Register it either with `@register_exporter`
or, from a separate package, under the `mems_sketch.exporters` entry-point group:

```toml
[project.entry-points."mems_sketch.exporters"]
svg = "my_package.svg:SvgExporter"
```

## MATLAB

MATLAB can call the library directly through its Python interface, e.g.
`d = py.mems_sketch.Design(name="demo");`.
