#pragma once

#include <stddef.h>

/* A parsed OBS locale file (Key="value" lines), without libobs. Used for the
 * English text compiled into each plugin, so labels and hints still show when
 * the data folder is missing. */

#ifdef __cplusplus
extern "C" {
#endif

struct text_table;

/* Parses the text of a locale file. Follows libobs' rules: blank lines and
 * lines starting with # or ; are skipped, quotes around the value are
 * removed, and \n, \t, \r and \" are unescaped. Returns NULL only when out of
 * memory. */
struct text_table *text_table_parse(const char *ini);

/* The value for key, or NULL if the file has no such key. */
const char *text_table_get(const struct text_table *t, const char *key);

size_t text_table_count(const struct text_table *t);
const char *text_table_key(const struct text_table *t, size_t i);

void text_table_free(struct text_table *t);

#ifdef __cplusplus
}
#endif
