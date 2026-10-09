# The Python package's build (scikit-build-core, see pyproject.toml): the
# geometry library's Python module, mems_sketch._geom, when Open CASCADE is
# found. Without it the package is pure Python and the rest of mems-sketch
# works as before, so contributors who never touch the C++ need no compiler.
#
# Release wheels set MEMS_SKETCH_REQUIRE_GEOMETRY=ON, so a wheel can never
# silently lack the module. Open CASCADE comes from CMAKE_PREFIX_PATH, or from
# build/deps/ where src/geom/scripts/build-occt.sh puts it.
cmake_minimum_required(VERSION 3.28)
project(mems_sketch LANGUAGES NONE)

option(MEMS_SKETCH_REQUIRE_GEOMETRY "Fail when the geometry library cannot be built" OFF)

file(GLOB _occt_builds "${CMAKE_CURRENT_SOURCE_DIR}/build/deps/occt-*")
list(APPEND CMAKE_PREFIX_PATH ${_occt_builds})

if(MEMS_SKETCH_REQUIRE_GEOMETRY)
    find_package(OpenCASCADE CONFIG REQUIRED)
else()
    find_package(OpenCASCADE CONFIG QUIET)
endif()

if(OpenCASCADE_FOUND)
    message(STATUS "Open CASCADE ${OpenCASCADE_VERSION}: building mems_sketch._geom")
    set(MGEOM_BUILD_PYTHON ON CACHE BOOL "" FORCE)
    set(MGEOM_BUILD_TESTS OFF CACHE BOOL "" FORCE)
    add_subdirectory(src/geom)
    install(TARGETS _geom LIBRARY DESTINATION mems_sketch)
    install(FILES "${CMAKE_CURRENT_BINARY_DIR}/src/geom/_geom.pyi" DESTINATION mems_sketch)
else()
    message(STATUS "Open CASCADE not found: mems_sketch without its geometry library "
                   "(pure Python; see src/geom/README.md to build it)")
endif()
