# Converts a text file into a C header holding it as a null-terminated char array.
# Usage: cmake -DINPUT=<file> -DOUTPUT=<header> -DSYMBOL=<name> -P embed-effect.cmake

file(READ "${INPUT}" content HEX)
string(REGEX REPLACE "([0-9a-f][0-9a-f])" "0x\\1," bytes "${content}")
string(
  REGEX REPLACE "(0x..,0x..,0x..,0x..,0x..,0x..,0x..,0x..,0x..,0x..,0x..,0x..,0x..,0x..,0x..,0x..,)"
  "\\1\n\t"
  bytes
  "${bytes}"
)

file(
  WRITE "${OUTPUT}.tmp"
  "/* Generated from ${INPUT} - do not edit. */\n#pragma once\nstatic const char ${SYMBOL}[] = {\n\t${bytes}0x00};\n"
)
file(COPY_FILE "${OUTPUT}.tmp" "${OUTPUT}" ONLY_IF_DIFFERENT)
file(REMOVE "${OUTPUT}.tmp")
