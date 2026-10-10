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
- **Building** (`include/mems/build.hpp`): components evaluated into the
  geometry library's regions, per layer: every shape kind and modifier,
  alignment, point coordinates in expressions, components' declared
  points, and components on levels of the layer stack (`src/model/levels.cpp`).
  Built-in components too, from their definitions; imported cells are still
  Python's (`NotSupported`).
  `tests/test_engine_build.py` compares the geometry with the Python
  backend's on the examples, hand-written components and random shape
  trees.

## Building

Needs a C++20 compiler, CMake 3.28 or later, Ninja and doctest, and for the
Python module the Python with nanobind from the
[library's setup](../geom/README.md#building). CMake fetches
[nlohmann/json](https://github.com/nlohmann/json) at a fixed commit. The
engine builds the geometry library with it, so it needs Open CASCADE too
(`CMAKE_PREFIX_PATH`, as for the library).

```bash
cmake -S src/engine -B build/engine -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH="$PWD/build/deps/occt-8_0_1" \
  -DMEMS_ENGINE_BUILD_PYTHON=ON -DPython_EXECUTABLE="$PWD/build/pyenv/bin/python"
cmake --build build/engine
ctest --test-dir build/engine --output-on-failure
PYTHONPATH=build/engine MGEOM_REQUIRED=1 pytest -q tests/test_engine_expressions.py tests/test_engine_model.py tests/test_engine_build.py
```

The package's build (`pip install -e .`) makes `mems_sketch._core` together
with `mems_sketch._geom` whenever Open CASCADE is found.

## Rules

- Only `mgeom`'s public headers (`include/mgeom/`), never Open CASCADE's,
  and nothing in `src/geom/` includes the engine's
  (`tests/test_architecture.py` checks both).
- Results must equal the Python backend's while both exist: every part
  that moves here gets an equivalence test against the code it replaces.
