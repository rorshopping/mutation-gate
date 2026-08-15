cmake_minimum_required(VERSION 3.16)
project(demo_cpp CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(math STATIC src/math.cpp)
target_include_directories(math PUBLIC src)

enable_testing()
add_executable(test_math tests/test_math.cpp)
target_link_libraries(test_math PRIVATE math)
add_test(NAME math_tests COMMAND test_math)
