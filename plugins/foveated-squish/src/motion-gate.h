#pragma once

/* Motion gate: turns per-frame motion measurements into a squish level 0..3
 * (0 = off, the picture is sent flat; 3 = strongest squish, the center is kept
 * and the edges are given up). Thresholds have hysteresis so the level doesn't
 * flicker, and a level must be held for a minimum time before it changes, so
 * the viewer gets a few well-spaced changes instead of a stream of them. */

#include <stdbool.h>

#define MOTION_LEVELS 4

struct motion_sample {
	double mean_flow_px;  /* mean flow magnitude, px per frame at flow resolution */
	double match_quality; /* fraction of blocks with a real match, 0..1 */
};

struct motion_gate_config {
	double flow_full_px;            /* flow magnitude that counts as full motion (m = 1) */
	double up[MOTION_LEVELS - 1];   /* rise to level i+1 when m >= up[i] */
	double down[MOTION_LEVELS - 1]; /* fall to level i when m < down[i] (below up[i]) */
	double hold_ms;                 /* minimum time a level is kept before it changes */
};

struct motion_gate {
	struct motion_gate_config cfg;
	int level;
	double held_ms;
};

/* Defaults chosen for the 6-second test clips; tune from the live test. */
struct motion_gate_config motion_gate_default_config(void);
void motion_gate_init(struct motion_gate *g, const struct motion_gate_config *cfg);
/* Motion value m in 0..1 from one sample (0 when the match quality is too low to trust). */
double motion_value(const struct motion_gate_config *cfg, const struct motion_sample *s);
/* Feed one sample covering frame_ms. Returns true when the level changed. */
bool motion_gate_update(struct motion_gate *g, const struct motion_sample *s, double frame_ms);
