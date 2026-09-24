# File formats

## The project folder

`storage/project_files.py` reads and writes the folder:

```
project.yaml     format: mems-sketch/1, name, top (null: a library), libraries, imports
process.yaml     constants, layers: {name: {gds: [layer, datatype], min_width, min_space}}
components/      one file per component; private ones in their owner's folder
imports/         the imported files
```

The YAML is **canonical** (`storage/yaml_format.py`), so saving the same model
twice gives byte-identical files and a change shows as a small diff:

- fields equal to their default are left out (except a node's `kind`);
- keys keep a fixed order: `kind` and `name` first, `modifiers`, `align` and
  `enabled` last, everything else in declaration order;
- whole numbers are written without `.0`; lists of plain values (points, GDS
  numbers) on one line;
- unchanged files are not rewritten.

A component file is the component's pydantic model as data
(`examples/resonator/components/suspension.yaml`):

```yaml
name: suspension
description: Serpentine spring ending in an anchor pad
parameters:
- name: turns
  default: 3
  min: 1
  integer: true
shapes:
- kind: ref
  name: spring
  component: serpentine_spring
  params:
    turns: turns
- kind: ref
  name: anchor
  component: anchor
  params:
    size: 40
  align:
    point: bottom
    to: spring.end
    dy: -1
```

Older files still load: `repeat:` is read as an array modifier, `group` as
`transform`, and a layer's old `undercut` is ignored.

## One tree, many formats

Every other file format goes through **one mapping and one codec per format**,
so formats do not have to be maintained one by one:

1. **Documents** (`storage/document.py`) turn a project into a plain *tree*
   (dicts, lists, strings, numbers, booleans, None, plus `bytes` and `Matrix`)
   and back. They are built from the same pieces as the folder
   (`component_data`, `process_data`, `imports_data` and their
   `…_from_data`), so a new model field reaches every format by itself.
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
format: mems-sketch/1
name: resonator
top: top
libraries: {std: ../libraries/mems_std}     # relative to the document
process: {constants: {...}, layers: {...}}  # as process.yaml
imports: {padframe: {file, cell, layers, data: <bytes>}}
components: {top: {...}, comb/finger: {...}}   # as the component files, keyed by path
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
