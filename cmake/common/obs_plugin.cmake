# Helpers for building several OBS plugins from one repository.
#
# Each folder in plugins/ with a CMakeLists.txt is one plugin: it declares its
# own project(<name> VERSION ...) and calls add_obs_plugin(). Every plugin is
# a separate module with its own data folder, log prefix and version, and
# installs to its own folder (<name>/bin/64bit, <name>/data on Windows).

include_guard(GLOBAL)

# add_obs_plugin(<target> SOURCES <files...> [FRONTEND_API] [QT])
function(add_obs_plugin target)
  cmake_parse_arguments(PARSE_ARGV 1 _AOP "FRONTEND_API;QT" "" "SOURCES")

  add_library(${target} MODULE)
  target_sources(${target} PRIVATE ${_AOP_SOURCES})
  target_link_libraries(${target} PRIVATE OBS::libobs)

  # Log prefix and version come from the calling directory's project().
  configure_file("${CMAKE_SOURCE_DIR}/common/plugin-support.c.in" "${CMAKE_CURRENT_BINARY_DIR}/plugin-support.c" @ONLY)
  target_sources(
    ${target}
    PRIVATE
      "${CMAKE_CURRENT_BINARY_DIR}/plugin-support.c"
      "${CMAKE_SOURCE_DIR}/common/plugin-support.h"
      "${CMAKE_SOURCE_DIR}/common/plugin-text.c"
      "${CMAKE_SOURCE_DIR}/common/plugin-text.h"
      "${CMAKE_SOURCE_DIR}/common/text-table.c"
      "${CMAKE_SOURCE_DIR}/common/text-table.h"
  )
  target_include_directories(${target} PRIVATE "${CMAKE_SOURCE_DIR}/common" "${CMAKE_CURRENT_SOURCE_DIR}/src")

  # English labels and hints compiled in (plugin_embedded_locale), so the
  # settings panel reads the same without the data folder.
  target_embed_text(${target} data/locale/en-US.ini plugin_embedded_locale embedded-locale.h)

  if(_AOP_FRONTEND_API)
    find_package(obs-frontend-api REQUIRED)
    target_link_libraries(${target} PRIVATE OBS::obs-frontend-api)
  endif()

  if(_AOP_QT)
    find_package(Qt6 COMPONENTS Widgets Core)
    target_link_libraries(${target} PRIVATE Qt6::Core Qt6::Widgets)
    set_target_properties(
      ${target}
      PROPERTIES AUTOMOC ON AUTOUIC ON AUTORCC ON
    )
  endif()

  set_target_properties_plugin(${target} PROPERTIES OUTPUT_NAME ${target})
endfunction()

# target_embed_text(<target> <file> <symbol> <header>)
# Compiles a text file (e.g. a shader) into the plugin as a null-terminated
# C string, regenerated whenever the file changes.
function(target_embed_text target file symbol header)
  cmake_path(ABSOLUTE_PATH file BASE_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}" OUTPUT_VARIABLE _src)
  set(_out "${CMAKE_CURRENT_BINARY_DIR}/generated/${header}")
  add_custom_command(
    OUTPUT "${_out}"
    COMMAND
      "${CMAKE_COMMAND}" -DINPUT=${_src} -DOUTPUT=${_out} -DSYMBOL=${symbol} -P
      "${CMAKE_SOURCE_DIR}/cmake/embed-effect.cmake"
    DEPENDS "${_src}" "${CMAKE_SOURCE_DIR}/cmake/embed-effect.cmake"
    COMMENT "Embedding ${file}"
    VERBATIM
  )
  target_sources(${target} PRIVATE "${_out}")
  target_include_directories(${target} PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/generated")
endfunction()
