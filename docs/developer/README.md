# Developer guide

For working on mems-sketch, or using it from code. For using the editor, see
the [user guide](../user/README.md).

1. **[Architecture](architecture.md)**: backend and frontends, the compiler
   and its cache, editing sessions, the package layout.
2. **[Python API](scripting.md)**: building and changing projects from
   scripts, and from MATLAB.
3. **[File formats](file-formats.md)**: the project folder's YAML, one-file
   documents, geometry documents, and how formats are kept in step.
4. **[Extending](extending.md)**: new components, shape kinds, edit commands,
   tool windows, exporters and file formats.

## Working on the code

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest -q                     # GUI tests run offscreen; no windows open
python docs/screenshots.py    # regenerate docs/images after a visible GUI change
```

Branches, pull requests and review are in [CONTRIBUTING](../../CONTRIBUTING.md).
Design notes for larger changes are in
[`docs/superpowers/specs/`](../superpowers/specs/): why a feature is built the
way it is.
