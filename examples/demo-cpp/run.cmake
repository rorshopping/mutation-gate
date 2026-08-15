# Configure, build, and run the CTest suite. Invoked by mutation-gate via
# `cmake -P run.cmake` so it works with a single test_command on any platform.
execute_process(
    COMMAND cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
    RESULT_VARIABLE cfg
)
if(NOT cfg EQUAL 0)
    message(FATAL_ERROR "cmake configure failed")
endif()

execute_process(
    COMMAND cmake --build build
    RESULT_VARIABLE bld
)
if(NOT bld EQUAL 0)
    message(FATAL_ERROR "cmake build failed")
endif()

execute_process(
    COMMAND ctest --test-dir build --output-on-failure
    RESULT_VARIABLE tst
)
if(NOT tst EQUAL 0)
    message(FATAL_ERROR "tests failed")
endif()
