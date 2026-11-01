cmake_minimum_required(VERSION 3.28...3.30)

include("${CMAKE_CURRENT_SOURCE_DIR}/cmake/common/bootstrap.cmake" NO_POLICY_SCOPE)

project(${_name} VERSION ${_version})

include(compilerconfig)
include(defaults)
include(helpers)
include(obs_plugin)

find_package(libobs REQUIRED)

# Every folder in plugins/ that has a CMakeLists.txt is one plugin.
file(GLOB _plugin_dirs LIST_DIRECTORIES true "${CMAKE_CURRENT_SOURCE_DIR}/plugins/*")
list(SORT _plugin_dirs)
foreach(_dir IN LISTS _plugin_dirs)
  if(EXISTS "${_dir}/CMakeLists.txt")
    add_subdirectory("${_dir}")
  endif()
endforeach()
