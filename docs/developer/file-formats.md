# File formats

## The project folder

`storage/project_files.py` reads and writes the folder. Where files are says
nothing about what they are called: names come from the manifest and the
component files.

```
project.yaml                    the manifest (below)
processes/main/process.yaml     one folder per process
components/top/component.yaml   one folder per component
components/comb/component.yaml
components/comb/finger/component.yaml   private to comb, which lists it
imports/                        the imported files
```

A component folder can hold other files of its own (notes, pictures);
saving only writes and removes `component.yaml`. A listed file that is
missing is an error; a `component.yaml` or `process.yaml` the manifest does
not reach is reported (`Project.load_notes`) and not loaded.

### The manifest

A library's manifest is a project's, usually with `top: null`:

```yaml
format: mems-sketch/2
name: resonator
top: top                          # null: a library
libraries: {std: ../libraries/mems_std}
process: std.surface              # a process of its own (main), or a library's
overrides:                        # changes to a library's process
  constants: {undercut: 3}
  rules:
    device_min_width: {value: 1.5, reason: test structures}       # a change
    metal_space: {kind: min_space, layers: [metal], value: 5}     # an added rule
processes: {main: processes/main}
components: {top: components/top, suspension: components/suspension}
imports:
  padframe: {file: padframe.gds, cell: FRAME, layers: {1/0: device, 5/0: metal}}
```

Only shared components are listed here; each component lists its private
ones. A project using a library's process can change constants and rules,
and add rules, but not layers or the layer stack; when the library's process
changes, the project follows it except where it changed it (`with_changes`
and `changes_between` in `core/process.py`).

### Processes

```yaml
description: Single-layer surface micromachining
constants: {undercut: 2, min_gap: 2}
layers:
  device: {gds: [1, 0]}
  anchor: {gds: [2, 0]}
  metal: {gds: [3, 0]}
levels:                 # the layer stack, bottom to top
- layer: device
  roles: {anchor: anchor}
- {layer: metal}
default_level: device   # where a top component is; the first level when left out
grid: 0.001             # µm: the fab's grid; rule checks round the design onto it (default 0.001)
chord: 0.005            # µm: how far curves may stray when they are split (default 0.005)
rules:
  device_min_width: {kind: min_width, layers: [device], value: 2}
  device_release:
    kind: release
    layers: [device, anchor]
    undercut: process.undercut
    severity: warning
```

A rule is written by its name, with its kind, its layers, then its values as
keys of their own; `severity`, `enabled` and `message` only when they differ
from `error`, true and empty.

### Components

`storage/component_format.py`. Parameters, points and shapes are maps by
name; a parameter with only a default is `name: default`:

```yaml
description: Comb-driven resonator
level: device           # its default level of the layer stack, if it has one
parameters:
  plate: 160
  enclosure: {default: 5, max: size / 2, max_exclusive: true}
points:
  tip: {at: beam.right, x: 2}
private: {finger: finger}   # private components: their folders, relative to this one
shapes:
  mass: {ref: std.perforated_plate, size: plate, pitch: pitch}
  comb_top:
    ref: comb_drive
    fingers: 16
    rotation: 180
    align: {point: moving, to: mass.top, dy: -1}
  slot:
    kind: boolean
    op: subtract
    a:
      plate: {kind: rect, x1: 10, y1: 10}
    b:
      hole: {kind: circle, x: 5, y: 5, radius: 2}
waivers:
- rule: device_min_width
  box: [100, 0, 101, 30]
  reason: test structure
  fingerprint: 3f1c0e9b2a7d4c55
```

- A placement is `ref:` its component, its parameter values, then its own
  fields. When a parameter is named like one of those fields (`x`,
  `rotation`, `level`...), all its values go under `params:`.
- Shapes holding shapes (a boolean's `a` and `b`, a transform's `children`)
  hold maps too.
- Every shape has a name; one made without a name gets its kind and the
  lowest free number (`rect1`), a placement its component's name
  (`anchor1`).
- A parameter's `min` and `max` are numbers or expressions over the other
  parameters and process constants; `min_exclusive` or `max_exclusive`
  refuses the limit itself.
- A shape's `layer` is `level` (the default: the component's level),
  `level+1`, `level-1`, `level.anchor`, `level-1.anchor`, or a layer by name
  (`core/levels.py`); a `ref`'s `level` puts what it places on a level.
- `waivers` are accepted rule violations: the rule, the violation's box
  (µm), the reason, and a fingerprint of the geometry around it, which tells
  when the waiver has lapsed.

### Canonical YAML

`storage/yaml_format.py`: saving the same model twice gives byte-identical
files, and a change shows as a small diff.

- Fields equal to their default are left out (except a node's `kind`).
- Keys keep a fixed order: `kind` first, `modifiers`, `align` and `enabled`
  last, everything else in declaration order.
- Whole numbers are written without `.0`; lists of plain values, and maps of
  plain values short enough (72 characters), go on one line.
- Unchanged files are not rewritten.

## One tree, many formats

Every other file format goes through **one mapping and one codec per format**,
so formats do not have to be maintained one by one:

1. **Documents** (`storage/document.py`) turn a project into a plain *tree*
   (dicts, lists, strings, numbers, booleans, None, plus `bytes` and `Matrix`)
   and back. They are built from the same pieces as the folder
   (`component_data`, `process_data`, `manifest_data`, `imports_data` and
   their readers), so a new model field reaches every format by itself.
2. **Codecs** (`storage/formats/`) turn any tree into bytes and back. They
   know nothing about projects.

| Format | Dict | List | Bytes | Matrix | Other values |
|---|---|---|---|---|---|
| YAML | mapping | sequence | `!!binary` | list of rows | as YAML |
| JSON | object | array | `{"base64": …}` | list of rows | as JSON |
| XML | child elements named by key, or `<entry key="…">` | `<item>` children, `type="list"` | `type="base64"` | `type="matrix"`, `x y; x y` | text; else a `type` attribute: `int`, `float`, `bool`, `null`, `map` (an empty dict) |
| `.mat` | struct; if keys are not field names, `keys`/`values` cells marked `mems_sketch_map` | row cell | struct with one field, `mems_sketch_bytes` | double matrix | double (whole numbers read as ints), `logical`, `char`, `[]` for None |

Each codec reads back exactly what it wrote; YAML and JSON give a `Matrix` back
as a list of rows, so readers accept both (`formats.rows`). `.mat` uses scipy
(the `matlab` extra) and reads version 5/7 files, not `-v7.3`.

### Project documents

```yaml
format: mems-sketch/2
name: resonator
top: top
libraries: {std: ../libraries/mems_std}     # relative to the document
process: main                               # and overrides, as in project.yaml
processes: {main: {...}}                    # as process.yaml
imports: {padframe: {file, cell, layers, data: <bytes>}}
components: {top: {...}, comb: {..., private: {finger: {...}}}}   # as component.yaml
```

`load`/`save` choose by suffix (`storage.is_document`). The tests convert
`examples/resonator` to every format and back and compare the files byte for
byte.

### Geometry documents

What a component evaluates to, for other tools, and importable as a
component:

```yaml
format: mems-sketch-geometry/1
component: top
unit: um
parameters: {pitch: 20}
layers:
  device:
    gds: [1, 0]
    polygons:
    - hull: <N×2 matrix>        # x y rows in µm, not closed
      holes: [<N×2 matrix>, …]
points: {tip: {x: 10, y: 5}}
```

Reading is lenient for files written by hand or by MATLAB: a polygon can be a
bare N×2 matrix, `polygons` can be a single struct, and `gds` can be a `[1 0]`
row. On import (`editing/imports.py`) the geometry becomes an OASIS file with
one cell, `TOP`, so layer names survive and everything after that (the
dialog, re-import, `imports/`) is the same as for GDS.
