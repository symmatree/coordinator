# The capture control loop and the campod conventions, with no camera library in them.
# Added with add_subdirectory by each capture app; nlohmann_json comes from the app's
# prefix (depthai's install for the OAK).
find_package(nlohmann_json 3 CONFIG REQUIRED)
find_package(Threads REQUIRED)

add_library(capture_common STATIC conventions.cpp session.cpp)
target_include_directories(capture_common PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(capture_common PUBLIC nlohmann_json::nlohmann_json Threads::Threads)
target_compile_features(capture_common PUBLIC cxx_std_20)
target_compile_options(capture_common PRIVATE -Wall -Wextra)

foreach(t test_conventions test_session)
  add_executable(${t} ${t}.cpp)
  target_link_libraries(${t} PRIVATE capture_common)
  target_compile_options(${t} PRIVATE -Wall -Wextra)
endforeach()
