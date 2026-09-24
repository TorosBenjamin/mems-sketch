# One-file formats: JSON, XML, MATLAB and YAML

## Goal

- Import and export projects and geometry as JSON, XML and MATLAB `.mat`.
- The command line translates a project folder to one file and back.
- Adding formats, and changing the model, must not mean maintaining one mapping
  per format.

## Design: one tree, many codecs

**Trees and codecs** (`storage/formats/`)
- A *tree* is plain data: dicts, lists, strings, numbers, booleans and None, plus
  `bytes` and `Matrix` (rows of numbers).
- A codec is `dump(tree) -> bytes` and `load(bytes) -> tree`. It knows nothing
  about projects.
- Each codec reads back what it wrote, with one exception: YAML and JSON have no
  matrix type, so a `Matrix` comes back from them as a list of rows. Code that
  reads a tree accepts both (`rows()`).

**How each format stores a tree**

| Format | Dict | List | Bytes | Matrix | Other values |
|---|---|---|---|---|---|
| YAML | mapping | sequence | `!!binary` | list of rows | as YAML |
| JSON | object | array | `{"base64": …}` | list of rows | as JSON |
| XML | child elements named by their keys | `<item>` children, `type="list"` | `type="base64"` | `type="matrix"`, `x y; x y` | text for strings, else a `type` attribute (int, float, bool, null) |
| `.mat` | struct | row cell | struct with a `mems_sketch_bytes` field | double matrix | doubles (whole numbers read back as ints), `logical`, `char`, `[]` for None |

- **Keys that aren't names.** A key that is not an XML name (`comb/finger`,
  `5/0`) becomes `<entry key="…">` in XML. In `.mat`, a dict whose keys are not
  MATLAB field names becomes a struct with `keys` and `values` cells plus a
  `mems_sketch_map` marker.
- **Why a bytes wrapper in `.mat`.** scipy reads `logical` values back as
  `uint8`, the same type as bytes, so bytes are wrapped in their own struct to
  keep the two apart.

**Documents** (`storage/document.py`) are trees built from the same pieces the
project folder uses. `project_files` now exposes `component_data`, `process_data`
and `imports_data` and their `…_from_data` counterparts, and the folder writer
uses them too.

- A **project document** holds the project's name, top component, libraries,
  process, imports (with the imported files' bytes) and components.
  - A new model field reaches every format by itself.
  - Converting a folder to a file and back gives the same files. The tests check
    this on `examples/resonator` for every format.
- A **geometry document** (`mems-sketch-geometry/1`) holds:
  - `component`, `unit: um` and `parameters`;
  - `layers.<name>`: `gds` numbers and `polygons`, each `{hull, holes}` of N×2
    matrices;
  - `points`.

**Reading files written by other tools**
- A single polygon given without its struct (a bare N×2 matrix) is accepted.
- `polygons` may be a single polygon struct instead of a list.
- A row `[1 0]` becomes a list where the project model expects one.

## Where it shows

**Storage**
- `load(path)` opens a one-file project from its suffix.
- `save(project, "x.json")` writes one.
- Opening such a file in the app gives a copy that is saved as a folder, like a
  legacy `.mems` file.

**Command line**
- `convert SOURCE TARGET` works between folders and files in both directions, and
  still reads `.mems`. It refuses to overwrite a folder that already holds a
  project.
- `export` writes a geometry document when the target is `.json`, `.xml` or
  `.mat`.

**Exporters**
- One `DocumentExporter` class serves every format through the codec with the
  same name.
- Exporters that set `wants_context` also receive the component and its
  parameter values, so they can write the points.
- The built-in exporters are always registered, even when a stale install's
  entry points don't list them.

**Import**
- A geometry document is converted to an OASIS file with one cell, `TOP`. OASIS
  is used rather than GDS because it keeps layer names.
- From there it goes through the same path as a GDS import: the import dialog,
  re-import, and storage in `imports/`.
- Layers get their GDS numbers from the document. Without one, a layer takes the
  numbers of the project layer with the same name, else numbers nothing else
  uses.
- A new layer keeps the name it has in the document.

**Dependency**
- `.mat` needs scipy, which is an optional extra (`mems-sketch[matlab]`) and
  included in `dev`.
- Without scipy, `.mat` files fail with a message saying what to install.

## Not now

- MATLAB `-v7.3` files (HDF5).
- Converters to any particular in-house format; those belong with the tools that
  use them.
