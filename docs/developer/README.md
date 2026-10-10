# Developer guide

For working on mems-sketch, or using it from code. For using the editor, see
the [user guide](../user/README.md).

1. **[Architecture](architecture.md)**: backend and frontends, the engine
   and its cache, editing sessions, the package layout.
2. **[Python API](scripting.md)**: building and changing projects from
   scripts, and from MATLAB.
3. **[File formats](file-formats.md)**: the project folder's YAML, one-file
   documents, geometry documents, and how formats are kept in step.
4. **[Extending](extending.md)**: new components, shape kinds, edit commands,
   tool windows, exporters and file formats.
5. **[Requirements](requirements.md)**: what mems-sketch must do and how
   well, existing behaviour and new, with priorities and open questions.
6. **[Geometry core architecture](core-architecture.md)**: the C++ core the
   geometry backend is moving to, a geometry library on Open CASCADE with the
   mems-sketch engine on top; target architecture, with the migration plan.

## Working on the code

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest -q                     # GUI tests run offscreen; no windows open
python docs/screenshots.py    # regenerate docs/images after a visible GUI change
python benchmarks/gui_speed.py  # how fast pan, zoom, hover, drag and edits are on big layouts
```

`benchmarks/gui_speed.py` opens the editor offscreen on perforated plates with
100 to 90,000 holes and times each interaction (its docstring says how to
read the numbers). Run it before and after a change that could make the
editor slower, on the same machine.

Branches, pull requests and review are in [CONTRIBUTING](../../CONTRIBUTING.md).
Design notes for larger changes are in
[`docs/superpowers/specs/`](../superpowers/specs/): why a feature is built the
way it is.
