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

# Open CASCADE built by scripts/build-occt.sh: only the modules a 2D/2.5D
# geometry library needs, as static libraries. Pass its prefix as
# CMAKE_PREFIX_PATH.
find_package(OpenCASCADE CONFIG REQUIRED)
set(MGEOM_OCC_LIBRARIES
    TKernel TKMath TKG2d TKG3d TKGeomBase TKBRep TKGeomAlgo TKTopAlgo
    TKPrim TKBO TKBool TKShHealing TKOffset TKFillet TKMesh)

add_library(mgeom
    src/transform.cpp
    src/region.cpp)
add_library(mgeom::mgeom ALIAS mgeom)
target_include_directories(mgeom
    PUBLIC $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    PRIVATE ${OpenCASCADE_INCLUDE_DIR})
target_link_libraries(mgeom PRIVATE ${MGEOM_OCC_LIBRARIES})
set_target_properties(mgeom PROPERTIES POSITION_INDEPENDENT_CODE ON)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(mgeom PRIVATE -Wall -Wextra -Wpedantic)
endif()

if(MGEOM_BUILD_TESTS)
    enable_testing()
    find_package(doctest REQUIRED)
    add_executable(mgeom_tests
        tests/main.cpp
        tests/test_transform.cpp
        tests/test_region.cpp)
    target_link_libraries(mgeom_tests PRIVATE mgeom doctest::doctest)
    add_test(NAME mgeom_tests COMMAND mgeom_tests)
endif()
