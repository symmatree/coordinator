cmake_minimum_required(VERSION 3.25)
project(oak_capture LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# depthai from the base image (containers/oak-capture-base). Its package brings the
# nlohmann_json it was built with, which this uses too.
find_package(depthai CONFIG REQUIRED)
find_package(Threads REQUIRED)

# The campod conventions, with no depthai in them.
add_library(conventions STATIC src/conventions.cpp)
target_include_directories(conventions PUBLIC src)
target_link_libraries(conventions PUBLIC depthai::nlohmann_json Threads::Threads)
target_compile_options(conventions PRIVATE -Wall -Wextra)

add_executable(oak-capture src/main.cpp)
target_link_libraries(oak-capture PRIVATE conventions depthai::core)
target_compile_options(oak-capture PRIVATE -Wall -Wextra)

add_executable(test_conventions src/test_conventions.cpp)
target_link_libraries(test_conventions PRIVATE conventions)
target_compile_options(test_conventions PRIVATE -Wall -Wextra)
