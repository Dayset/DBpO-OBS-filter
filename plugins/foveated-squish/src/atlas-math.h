#pragma once

/* Sharp-region atlas, C port of research/foveation/atlas_math.py (spec:
 * docs/atlas-spec.md). Regions are source rectangles packed into the strip
 * beside the squished picture as slots, each with a 4 px gutter. Config keys
 * are a letter per region (g, h, i, j) and a field (x, y, z = x1, v = y1, s =
 * scale), e.g. "gx=0.800,gy=0.850,gz=1.000,gv=1.000,gs=1.000". Unknown keys are
 * ignored by squish_config_parse(), so older parsers still load the squish. */

#include <stdbool.h>
#include <stddef.h>

#include "squish-math.h"

#define ATLAS_MAX_REGIONS 4
#define ATLAS_GUTTER 4
#define ATLAS_SNAP 8
#define ATLAS_STRIP_CAP 0.20
#define ATLAS_REGION_MIN 0.02
#define ATLAS_REGION_MAX 0.5

struct atlas_region {
	double x0, y0, x1, y1;
	double s; /* scale, 1.0 = 1:1 with source pixels */
};

struct atlas {
	struct squish_config squish;
	int count;
	struct atlas_region regions[ATLAS_MAX_REGIONS];
};

struct atlas_slot {
	int x, y, w, h; /* slot top-left after the gutter, and size in stream pixels */
	double scale;   /* scale actually used after fitting */
};

/* Parses the squish part and any region keys. Returns false if invalid. */
bool atlas_parse(const char *s, struct atlas *a);
/* Smallest scale that still gives at least 50% less squish at the region centre. */
double atlas_region_floor(const struct atlas *a, const struct atlas_region *r);
/* Deterministic shelf packing (spec section 3). Output: one slot per region.
 * Returns false if the regions cannot fit above the density floor. */
bool atlas_layout(const struct atlas *a, int out_w, int out_h, int src_w, int src_h, struct atlas_slot *slots);
