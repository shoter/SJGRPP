# Profile-guided optimisation of the game binary (opt-in, off by default).
#
#   OPTION_PGO=GENERATE  build an instrumented binary; running it writes profile data into OPTION_PGO_DIR
#   OPTION_PGO=USE       optimise with the profile data in OPTION_PGO_DIR
#
# Typical use (see utils/debug_harness/pgo_train.py for the training workload):
#   cmake -DOPTION_PGO=GENERATE .. && cmake --build . --target openttd
#   python utils/debug_harness/pgo_train.py --exe ./openttd --pgo-dir pgo
#   cmake -DOPTION_PGO=USE .. && cmake --build . --target openttd
#
# MSVC: needs whole-program optimisation (IPO/LTCG, on by default for non-Debug builds). Code generation happens at
#       link time, so switching GENERATE -> USE only relinks. The instrumented binary needs pgort140.dll (from the
#       MSVC bin directory) and writes openttd!N.pgc next to openttd.pgd in OPTION_PGO_DIR (set VCPROFILE_PATH to
#       that directory when running it); the USE link merges them.
# GCC:  -fprofile-generate / -fprofile-use. Build GENERATE and USE in the same build directory, as the profile file
#       names are derived from the object file paths.
# Clang: -fprofile-generate / -fprofile-use; the .profraw files must be merged into OPTION_PGO_DIR/default.profdata
#       with llvm-profdata first (utils/debug_harness/pgo_train.py does this).
#
# Neither changes floating-point semantics: profile data only steers inlining, code layout and branch ordering.

set(OPTION_PGO "" CACHE STRING "Profile-guided optimisation of the game binary: empty (off), GENERATE or USE")
set_property(CACHE OPTION_PGO PROPERTY STRINGS "" GENERATE USE)
set(OPTION_PGO_DIR "${CMAKE_BINARY_DIR}/pgo" CACHE PATH "Directory for profile-guided optimisation data")

function(setup_pgo)
    if(NOT OPTION_PGO)
        return()
    endif()
    string(TOUPPER "${OPTION_PGO}" PGO_MODE)
    if(NOT PGO_MODE STREQUAL "GENERATE" AND NOT PGO_MODE STREQUAL "USE")
        message(FATAL_ERROR "OPTION_PGO must be empty, GENERATE or USE (got '${OPTION_PGO}')")
    endif()
    file(TO_CMAKE_PATH "${OPTION_PGO_DIR}" PGO_DIR)
    file(MAKE_DIRECTORY "${PGO_DIR}")

    if(MSVC AND CMAKE_CXX_COMPILER_ID STREQUAL "MSVC")
        if(NOT IPO_FOUND OR OPTION_NO_LTO OR CMAKE_BUILD_TYPE STREQUAL "Debug")
            message(FATAL_ERROR "OPTION_PGO with MSVC needs whole-program optimisation (a non-Debug build without OPTION_NO_LTO)")
        endif()
        file(TO_NATIVE_PATH "${PGO_DIR}/openttd.pgd" PGD)
        if(PGO_MODE STREQUAL "GENERATE")
            target_link_options(openttd PRIVATE "/GENPROFILE:PGD=${PGD}")
        else()
            if(NOT EXISTS "${PGO_DIR}/openttd.pgd")
                message(FATAL_ERROR "OPTION_PGO=USE: ${PGO_DIR}/openttd.pgd does not exist; build and train with OPTION_PGO=GENERATE first")
            endif()
            # Profiles from a different build are reported and ignored per function rather than failing the link.
            target_link_options(openttd PRIVATE "/USEPROFILE:PGD=${PGD}")
        endif()
    elseif(CMAKE_CXX_COMPILER_ID STREQUAL "GNU" OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
        if(PGO_MODE STREQUAL "GENERATE")
            # The game is multi-threaded; atomic counter updates keep the profile consistent.
            set(PGO_FLAGS "-fprofile-generate=${PGO_DIR}" "-fprofile-update=atomic")
        elseif(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
            # Code the training run did not reach is optimised as usual instead of for size.
            set(PGO_FLAGS "-fprofile-use=${PGO_DIR}" "-fprofile-partial-training" "-Wno-missing-profile")
        else()
            if(NOT EXISTS "${PGO_DIR}/default.profdata")
                message(FATAL_ERROR "OPTION_PGO=USE: ${PGO_DIR}/default.profdata does not exist; merge the .profraw files with llvm-profdata first")
            endif()
            set(PGO_FLAGS "-fprofile-use=${PGO_DIR}/default.profdata" "-Wno-profile-instr-unprofiled" "-Wno-profile-instr-out-of-date")
        endif()
        foreach(TGT openttd_lib openttd openttd_test)
            if(TARGET ${TGT})
                target_compile_options(${TGT} PRIVATE ${PGO_FLAGS})
                target_link_options(${TGT} PRIVATE ${PGO_FLAGS})
            endif()
        endforeach()
    else()
        message(FATAL_ERROR "OPTION_PGO is not supported for compiler ${CMAKE_CXX_COMPILER_ID}")
    endif()
    message(STATUS "Option PGO - ${PGO_MODE} (${PGO_DIR})")
endfunction()
