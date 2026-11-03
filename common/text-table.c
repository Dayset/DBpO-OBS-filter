#include "text-table.h"

#include <stdlib.h>
#include <string.h>

struct text_entry {
	char *key;
	char *value;
};

struct text_table {
	struct text_entry *entries;
	size_t count;
	size_t capacity;
};

static char *copy_range(const char *start, size_t len)
{
	char *s = malloc(len + 1);
	if (s) {
		memcpy(s, start, len);
		s[len] = 0;
	}
	return s;
}

/* libobs unescapes \n, \t, \r and \" in values and leaves anything else. */
static char *unescape(const char *start, size_t len)
{
	char *s = malloc(len + 1), *d = s;
	if (!s)
		return NULL;
	for (size_t i = 0; i < len; i++) {
		if (start[i] == '\\' && i + 1 < len) {
			const char next = start[i + 1];
			const char out = next == 'n'   ? '\n'
					 : next == 't' ? '\t'
					 : next == 'r' ? '\r'
					 : next == '"' ? '"'
						       : 0;
			if (out) {
				*d++ = out;
				i++;
				continue;
			}
		}
		*d++ = start[i];
	}
	*d = 0;
	return s;
}

static int is_space(char c)
{
	return c == ' ' || c == '\t' || c == '\r';
}

static int add_entry(struct text_table *t, char *key, char *value)
{
	if (!key || !value) {
		free(key);
		free(value);
		return 0;
	}
	if (t->count == t->capacity) {
		const size_t cap = t->capacity ? t->capacity * 2 : 64;
		struct text_entry *grown = realloc(t->entries, cap * sizeof(*grown));
		if (!grown) {
			free(key);
			free(value);
			return 0;
		}
		t->entries = grown;
		t->capacity = cap;
	}
	t->entries[t->count].key = key;
	t->entries[t->count].value = value;
	t->count++;
	return 1;
}

struct text_table *text_table_parse(const char *ini)
{
	struct text_table *t = calloc(1, sizeof(*t));
	if (!t || !ini)
		return t;

	const char *p = ini;
	while (*p) {
		const char *line = p;
		const char *end = strchr(p, '\n');
		if (!end)
			end = p + strlen(p);
		p = *end ? end + 1 : end;

		while (line < end && is_space(*line))
			line++;
		if (line == end || *line == '#')
			continue;
		const char *eq = memchr(line, '=', (size_t)(end - line));
		if (!eq)
			continue;
		const char *key_end = eq;
		while (key_end > line && is_space(key_end[-1]))
			key_end--;
		if (key_end == line)
			continue;

		const char *v = eq + 1;
		while (v < end && is_space(*v))
			v++;
		const char *v_end = end;
		if (v < end && *v == '"') {
			/* Quoted: up to the closing quote that isn't escaped. */
			v++;
			const char *q = v;
			while (q < end && *q != '"') {
				if (*q == '\\' && q + 1 < end)
					q++;
				q++;
			}
			v_end = q;
		} else {
			while (v_end > v && is_space(v_end[-1]))
				v_end--;
		}
		if (!add_entry(t, copy_range(line, (size_t)(key_end - line)), unescape(v, (size_t)(v_end - v))))
			break;
	}
	return t;
}

const char *text_table_get(const struct text_table *t, const char *key)
{
	if (!t || !key)
		return NULL;
	/* Last definition wins, as in libobs. */
	for (size_t i = t->count; i > 0; i--)
		if (strcmp(t->entries[i - 1].key, key) == 0)
			return t->entries[i - 1].value;
	return NULL;
}

size_t text_table_count(const struct text_table *t)
{
	return t ? t->count : 0;
}

const char *text_table_key(const struct text_table *t, size_t i)
{
	return t && i < t->count ? t->entries[i].key : NULL;
}

void text_table_free(struct text_table *t)
{
	if (!t)
		return;
	for (size_t i = 0; i < t->count; i++) {
		free(t->entries[i].key);
		free(t->entries[i].value);
	}
	free(t->entries);
	free(t);
}
