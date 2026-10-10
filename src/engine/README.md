# The mems-sketch engine

Turns a project into geometry, on the geometry library
([`src/geom/`](../geom/README.md); see the
[core architecture](../../docs/developer/core-architecture.md)). It will hold
the project model, the shape kinds, modifiers and alignments, and decide what
is rebuilt; the Python package reaches it as **`mems_sketch._core`**, behind
`mems_sketch.engine`.

**Status:** nothing in mems-sketch uses it yet.

- **Expressions** (`include/mems/expression.hpp`): the language of
  `mems_sketch.core.expressions` with Python's arithmetic, to the last bit.
  `tests/test_engine_expressions.py` compares the two on every expression in
  the examples and twenty thousand generated ones.
- **The project model** (`include/mems/project.hpp`): a project read from
  JSON (`mems_sketch.engine.project_data`), with its process constants,
  component names resolved as `mems_sketch.core.project` resolves them,
  reference checks, parameter values and fingerprints.
  `tests/test_engine_model.py` compares it with Python's.

## Building

Needs a C++20 compiler, CMake 3.28 or later, Ninja and doctest, and for the
Python module the Python with nanobind from the
[library's setup](../geom/README.md#building). CMake fetches
[nlohmann/json](https://github.com/nlohmann/json) at a fixed commit. The
engine does not use Open CASCADE yet.

```bash
cmake -S src/engine -B build/engine -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DMEMS_ENGINE_BUILD_PYTHON=ON -DPython_EXECUTABLE="$PWD/build/pyenv/bin/python"
cmake --build build/engine
ctest --test-dir build/engine --output-on-failure
PYTHONPATH=build/engine MGEOM_REQUIRED=1 pytest -q tests/test_engine_expressions.py tests/test_engine_model.py
```

The package's build (`pip install -e .`) makes `mems_sketch._core` together
with `mems_sketch._geom` whenever Open CASCADE is found.

## Rules

- Only `mgeom`'s public headers (`include/mgeom/`), never Open CASCADE's,
  and nothing in `src/geom/` includes the engine's
  (`tests/test_architecture.py` checks both).
- Results must equal the Python backend's while both exist: every part
  that moves here gets an equivalence test against the code it replaces.
