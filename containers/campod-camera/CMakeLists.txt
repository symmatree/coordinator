cmake_minimum_required(VERSION 3.25)
project(campod_capture LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# libcamera from the Raspberry Pi archive, the same build the runtime stage installs;
# libjpeg-turbo and nlohmann_json from Debian.
find_package(PkgConfig REQUIRED)
pkg_check_modules(LIBCAMERA REQUIRED IMPORTED_TARGET libcamera)
pkg_check_modules(TURBOJPEG REQUIRED IMPORTED_TARGET libturbojpeg)

add_subdirectory(../capture-common capture-common)

add_executable(campod-capture src/capture.cpp)
target_link_libraries(campod-capture PRIVATE capture_common PkgConfig::LIBCAMERA PkgConfig::TURBOJPEG)
target_compile_options(campod-capture PRIVATE -Wall -Wextra)

add_executable(test_campod src/test_campod.cpp)
target_compile_options(test_campod PRIVATE -Wall -Wextra)
