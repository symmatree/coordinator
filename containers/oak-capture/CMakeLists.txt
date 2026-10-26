cmake_minimum_required(VERSION 3.25)
project(oak_capture LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# depthai from the base image (containers/oak-capture-base).
find_package(depthai CONFIG REQUIRED)

add_subdirectory(../capture-common capture-common)

add_executable(oak-capture src/main.cpp)
target_link_libraries(oak-capture PRIVATE capture_common depthai::core)
target_compile_options(oak-capture PRIVATE -Wall -Wextra)
