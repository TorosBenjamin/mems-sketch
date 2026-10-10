# The mems-sketch engine: turns a project into geometry, on the geometry
# library (see docs/developer/core-architecture.md). It includes only mgeom's
# public headers; nothing in src/geom/ includes the engine's.
#
# So far: expressions (the same language and values as
# mems_sketch.core.expressions), the project model (names, parameters,
# fingerprints, as mems_sketch.core.project and compiler have them), and
# building the first shape kinds. Needs Open CASCADE for mgeom: pass its
# prefix as CMAKE_PREFIX_PATH.
cmake_minimum_required(VERSION 3.28)
project(mems_engine VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(MEMS_ENGINE_BUILD_TESTS "Build the engine's unit tests" ON)
option(MEMS_ENGINE_BUILD_PYTHON "Build the Python module _core" OFF)

# nlohmann/json (MIT): projects arrive as JSON. Fetched at a fixed commit,
# header-only, nothing installed.
include(FetchContent)
FetchContent_Declare(nlohmann_json
    GIT_REPOSITORY https://github.com/nlohmann/json.git
    GIT_TAG 9cca280a4d0ccf0c08f47a99aa71d1b0e52f8d03  # v3.11.3
    EXCLUDE_FROM_ALL)
set(JSON_Install OFF CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(nlohmann_json)

# The geometry library, unless the package's build has added it already.
if(NOT TARGET mgeom)
    set(MGEOM_BUILD_TESTS OFF CACHE BOOL "" FORCE)
    add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../geom ${CMAKE_CURRENT_BINARY_DIR}/mgeom)
endif()

add_library(mems_engine
    src/expr/expression.cpp
    src/model/project.cpp
    src/model/levels.cpp
    src/util/sha256.cpp
    src/eval/evaluator.cpp
    src/shapes/kinds.cpp
    src/shapes/rect.cpp
    src/shapes/polygon.cpp
    src/shapes/circle.cpp
    src/shapes/boolean.cpp
    src/shapes/transform.cpp
    src/shapes/ref.cpp
    src/shapes/arcs.cpp
    src/shapes/arc.cpp
    src/shapes/path.cpp
    src/shapes/guide.cpp
    src/shapes/offset.cpp
    src/shapes/fillet.cpp
    src/shapes/layer_map.cpp
    src/modifiers/modifiers.cpp
    src/modifiers/array.cpp
    src/modifiers/polar_array.cpp
    src/modifiers/mirror.cpp
    src/modifiers/corners.cpp)
add_library(mems::engine ALIAS mems_engine)
target_include_directories(mems_engine
    PUBLIC $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>)
target_link_libraries(mems_engine PUBLIC mgeom PRIVATE nlohmann_json::nlohmann_json)
set_target_properties(mems_engine PROPERTIES POSITION_INDEPENDENT_CODE ON)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    # Python does not fuse a * b + c into one rounding; neither may we.
    target_compile_options(mems_engine PRIVATE -Wall -Wextra -Wpedantic -ffp-contract=off)
elseif(MSVC)
    target_compile_options(mems_engine PRIVATE /fp:precise)
endif()

if(MEMS_ENGINE_BUILD_TESTS)
    enable_testing()
endif()

# The Python module mems_sketch._core (nanobind).
if(MEMS_ENGINE_BUILD_PYTHON)
    find_package(Python 3.11 REQUIRED COMPONENTS Interpreter Development.Module)
    if(NOT nanobind_ROOT)
        execute_process(
            COMMAND "${Python_EXECUTABLE}" -m nanobind --cmake_dir
            OUTPUT_STRIP_TRAILING_WHITESPACE OUTPUT_VARIABLE nanobind_ROOT
            COMMAND_ERROR_IS_FATAL ANY)
    endif()
    find_package(nanobind CONFIG REQUIRED)
    nanobind_add_module(_core NB_STATIC bindings/core.cpp)
    target_link_libraries(_core PRIVATE mems_engine)
    target_compile_definitions(_core PRIVATE MEMS_ENGINE_VERSION="${PROJECT_VERSION}")
    nanobind_add_stub(_core_stub MODULE _core OUTPUT _core.pyi
        PYTHON_PATH $<TARGET_FILE_DIR:_core> DEPENDS _core)
endif()

if(MEMS_ENGINE_BUILD_TESTS)
    find_package(doctest REQUIRED)
    add_executable(mems_engine_tests
        tests/main.cpp
        tests/test_expression.cpp
        tests/test_project.cpp
        tests/test_build.cpp)
    target_link_libraries(mems_engine_tests PRIVATE mems_engine doctest::doctest)
    add_test(NAME mems_engine_tests COMMAND mems_engine_tests)
endif()
