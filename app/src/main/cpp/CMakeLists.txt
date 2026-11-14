# CMakeLists.txt — Uncharted: Golden Abyss (Unofficial Port) native module
#
# Default (CI, repo checkout) = STUB: launcher + ImGui overlay saja, tanpa
# konten game. Untuk build FULL (game berjalan), kode hasil rekompilasi harus
# digenerate dari dump MILIK SENDIRI ke folder cpp/gen/ (lihat README):
#   tools/gen_local.sh        (menyalin recomp.cpp/h + gen/recompiled_part*.cpp)
# File-file itu di-gitignore dan TIDAK PERNAH boleh di-commit/redistribusi.

cmake_minimum_required(VERSION 3.22.1)
project(uncharted CXX C)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -Wall")

# ---- Dear ImGui (pinned) ---------------------------------------------------
include(FetchContent)
FetchContent_Declare(
    imgui
    GIT_REPOSITORY https://github.com/ocornut/imgui.git
    GIT_TAG v1.91.9
)
FetchContent_GetProperties(imgui)
if(NOT imgui_POPULATED)
    FetchContent_Populate(imgui)
endif()
set(IMGUI_DIR ${imgui_SOURCE_DIR})
set(IMGUI_BACKENDS ${imgui_SOURCE_DIR}/backends)

add_library(imgui STATIC
    ${IMGUI_DIR}/imgui.cpp
    ${IMGUI_DIR}/imgui_draw.cpp
    ${IMGUI_DIR}/imgui_tables.cpp
    ${IMGUI_DIR}/imgui_widgets.cpp
    ${IMGUI_DIR}/imgui_demo.cpp
    ${IMGUI_BACKENDS}/imgui_impl_android.cpp
    ${IMGUI_BACKENDS}/imgui_impl_opengl3.cpp
)
target_include_directories(imgui PUBLIC ${IMGUI_DIR} ${IMGUI_BACKENDS})
target_link_libraries(imgui PUBLIC android log EGL GLESv3)

# ---- native_app_glue from the NDK ------------------------------------------
find_program(CCACHE_PROGRAM ccache)
set(_glue_dir "${ANDROID_NDK}/sources/android/native_app_glue")
if(NOT EXISTS "${_glue_dir}/android_native_app_glue.c")
    # Fallback: some NDK layouts nest under build/../sources
    set(_glue_dir "${ANDROID_NDK_HOME}/sources/android/native_app_glue")
endif()
add_library(app-glue STATIC ${_glue_dir}/android_native_app_glue.c)
target_include_directories(app-glue PUBLIC ${_glue_dir})
target_link_libraries(app-glue PUBLIC android log)

# ---- Game code: only if locally generated (NEVER committed) -----------------
file(GLOB RC_GAME_PARTS ${CMAKE_CURRENT_SOURCE_DIR}/gen/recompiled_part[0-9][0-9][0-9].cpp)
if(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/recomp.cpp AND
   EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/gen/recompiled_parts.cpp AND
   RC_GAME_PARTS)
    set(RC_WITH_GAME_CODE 1)
    message(STATUS "Uncharted native: FULL build (game code found locally)")
else()
    set(RC_WITH_GAME_CODE 0)
    message(STATUS "Uncharted native: STUB build (no game code; CI/default)")
endif()

# ---- Ouput library -----------------------------------------------------------
set(UNCHARTED_SOURCES
    android_main.cpp
    ui_overlay.cpp
)

add_library(uncharted SHARED ${UNCHARTED_SOURCES})
target_include_directories(uncharted PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
# NativeActivity mencari ANativeActivity_onCreate via dlsym; tanpa -u linker
# membuangnya (static lib) -> force close. Paksa simbol ini masuk & diekspor.
target_link_options(uncharted PRIVATE -Wl,-u,ANativeActivity_onCreate)
target_link_libraries(uncharted
    PRIVATE
    -Wl,--whole-archive
    app-glue
    -Wl,--no-whole-archive
    imgui
    android
    log
    EGL
    GLESv3
)
target_compile_definitions(uncharted PRIVATE RC_WITH_GAME_CODE=${RC_WITH_GAME_CODE})

if(RC_WITH_GAME_CODE)
    add_library(uncharted_game STATIC
        ${CMAKE_CURRENT_SOURCE_DIR}/recomp.cpp
        ${CMAKE_CURRENT_SOURCE_DIR}/gen/recompiled_parts.cpp
        ${RC_GAME_PARTS}
    )
    target_include_directories(uncharted_game PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
    # --whole-archive: tanpa ini linker membuang seluruh kode game (static lib)
    # karena tidak ada simbol yang direferensikan langsung -> .so cuma shell.
    target_link_libraries(uncharted PRIVATE
        -Wl,--whole-archive
        uncharted_game
        -Wl,--no-whole-archive
    )
    target_compile_definitions(uncharted PRIVATE RC_WITH_GAME_CODE=1)
endif()
