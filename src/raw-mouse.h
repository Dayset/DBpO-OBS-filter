#pragma once

#include <stdbool.h>
#include <stdint.h>

/* Process-wide raw mouse listener (Windows Raw Input, the same data games
 * read). Only relative movement is collected: no buttons, wheel or keys.
 * Shared by every filter instance; started on first acquire, stopped on
 * last release. On other platforms acquire() returns false. */

bool raw_mouse_supported(void);

/* Returns false if the listener could not start (another component in OBS
 * already owns raw mouse input, or the platform is unsupported). */
bool raw_mouse_acquire(void);
void raw_mouse_release(void);

/* Running totals of mouse counts since the listener started. They wrap;
 * subtract two readings as uint32_t and cast to int32_t to get a delta. */
void raw_mouse_read_totals(uint32_t *x, uint32_t *y);

/* Human-readable status for the settings dialog. */
const char *raw_mouse_status(void);
