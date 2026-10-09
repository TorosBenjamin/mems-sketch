# mgeom: 2D layered layout geometry on Open CASCADE.
#
# A library on its own: it never includes or links anything of the
# mems-sketch engine (see docs/developer/core-architecture.md).
cmake_minimum_required(VERSION 3.24)
project(mgeom VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(MGEOM_BUILD_TESTS "Build the mgeom unit tests" ON)
option(MGEOM_BUILD_PYTHON "Build the Python module _geom" OFF)

# Open CASCADE built by scripts/build-occt.sh: only the modules a 2D/2.5D
# geometry library needs, as static libraries. Pass its prefix as
# CMAKE_PREFIX_PATH.
find_package(OpenCASCADE CONFIG REQUIRED)
set(MGEOM_OCC_LIBRARIES
    TKernel TKMath TKG2d TKG3d TKGeomBase TKBRep TKGeomAlgo TKTopAlgo
    TKPrim TKBO TKBool TKShHealing TKOffset TKFillet TKMesh)

# Clipper2 (Boost licence): integer polygon booleans for snapping to a
# grid. Fetched at a fixed commit and built static.
include(FetchContent)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CLIPPER2_UTILS OFF CACHE BOOL "" FORCE)
set(CLIPPER2_EXAMPLES OFF CACHE BOOL "" FORCE)
set(CLIPPER2_TESTS OFF CACHE BOOL "" FORCE)
set(CLIPPER2_USINGZ OFF CACHE STRING "" FORCE)
FetchContent_Declare(Clipper2
    GIT_REPOSITORY https://github.com/AngusJohnson/Clipper2.git
    GIT_TAG 21ebba05db8894f0c7217ad35ea518080f324946  # Clipper2_2.0.1
    SOURCE_SUBDIR CPP)
FetchContent_MakeAvailable(Clipper2)

add_library(mgeom
    src/transform.cpp
    src/region.cpp
    src/cell.cpp
    src/measure.cpp
    src/modify.cpp
    src/wire.cpp
    src/construct.cpp
    src/snap.cpp)
add_library(mgeom::mgeom ALIAS mgeom)
target_include_directories(mgeom
    PUBLIC $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    PRIVATE ${OpenCASCADE_INCLUDE_DIR})
target_link_libraries(mgeom PRIVATE ${MGEOM_OCC_LIBRARIES} Clipper2)
set_target_properties(mgeom PROPERTIES POSITION_INDEPENDENT_CODE ON)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(mgeom PRIVATE -Wall -Wextra -Wpedantic)
endif()

if(MGEOM_BUILD_TESTS)
    enable_testing()
endif()

# The Python module mems_sketch._geom (nanobind). Needs a Python with
# nanobind installed; its tests also need NumPy and pytest.
if(MGEOM_BUILD_PYTHON)
    find_package(Python 3.11 REQUIRED COMPONENTS Interpreter Development.Module)
    execute_process(
        COMMAND "${Python_EXECUTABLE}" -m nanobind --cmake_dir
        OUTPUT_STRIP_TRAILING_WHITESPACE OUTPUT_VARIABLE nanobind_ROOT
        COMMAND_ERROR_IS_FATAL ANY)
    find_package(nanobind CONFIG REQUIRED)
    nanobind_add_module(_geom NB_STATIC bindings/geom.cpp)
    target_link_libraries(_geom PRIVATE mgeom)
    target_compile_definitions(_geom PRIVATE MGEOM_VERSION="${PROJECT_VERSION}")
    nanobind_add_stub(_geom_stub MODULE _geom OUTPUT _geom.pyi
        PYTHON_PATH $<TARGET_FILE_DIR:_geom> DEPENDS _geom)
    if(MGEOM_BUILD_TESTS)
        add_test(NAME mgeom_python
            COMMAND "${Python_EXECUTABLE}" -m pytest -q -p no:cacheprovider
                    "${CMAKE_CURRENT_SOURCE_DIR}/tests/python")
        set_tests_properties(mgeom_python PROPERTIES
            ENVIRONMENT "PYTHONPATH=$<TARGET_FILE_DIR:_geom>")
    endif()
endif()

if(MGEOM_BUILD_TESTS)
    find_package(doctest REQUIRED)
    add_executable(mgeom_tests
        tests/main.cpp
        tests/test_transform.cpp
        tests/test_region.cpp
        tests/test_cell.cpp
        tests/test_measure.cpp
        tests/test_modify.cpp
        tests/test_wire.cpp
        tests/test_snap.cpp)
    target_link_libraries(mgeom_tests PRIVATE mgeom doctest::doctest)
    add_test(NAME mgeom_tests COMMAND mgeom_tests)

    # Timings of the reference designs; run by hand, not by ctest.
    add_executable(mgeom_bench tests/bench.cpp)
    target_link_libraries(mgeom_bench PRIVATE mgeom)
endif()
