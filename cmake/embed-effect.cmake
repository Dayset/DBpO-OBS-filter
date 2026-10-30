# Converts a text file into a C header holding it as a null-terminated char array.
# Usage: cmake -DINPUT=<file> -DOUTPUT=<header> -DSYMBOL=<name> -P embed-effect.cmake

file(READ "${INPUT}" content HEX)
string(REGEX REPLACE "([0-9a-f][0-9a-f])" "0x\\1," bytes "${content}")

# Wrap every 16 bytes.
string(REPEAT "0x..," 16 row)
string(REGEX REPLACE "(${row})" "\\1\n\t" bytes "${bytes}")

set(header "/* Generated from ${INPUT} - do not edit. */\n#pragma once\n")
string(APPEND header "static const char ${SYMBOL}[] = {\n\t${bytes}0x00};\n")

file(WRITE "${OUTPUT}.tmp" "${header}")
file(COPY_FILE "${OUTPUT}.tmp" "${OUTPUT}" ONLY_IF_DIFFERENT)
file(REMOVE "${OUTPUT}.tmp")
