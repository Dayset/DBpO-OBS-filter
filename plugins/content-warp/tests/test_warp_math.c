#include <stdio.h>
#include <math.h>
#include <string.h>

#include "warp-math.h"

#define BINS 240
#define SRC_W 1280.0f
#define EW 960.0f /* 4:3 picture for a 720 high canvas */
#define P 0.2f /* 20% centre */
#define MAP_W 960

static int fails;
#define CHECK(c, msg)                          \
	do {                                   \
		if (!(c)) {                    \
			fprintf(stderr, "FAIL: %s\n", msg); \
			fails++;               \
		}                              \
	} while (0)

/* Fraction of left-side output pixels whose source position falls in bin b. */
static float left_share(const float *map, int b)
{
	int n = 0, hit = 0;
	float lo = (float)b / BINS, hi = (float)(b + 1) / BINS;
	float eL = (EW - P * SRC_W) * 0.5f;
	for (int x = 0; x < MAP_W; x++) {
		if ((float)x + 0.5f >= eL)
			break;
		n++;
		if (map[x] >= lo && map[x] < hi)
			hit++;
	}
	return n ? (float)hit / n : 0.0f;
}

int main(void)
{
	float flat[BINS], spike[BINS], zero[BINS], map[MAP_W];
	for (int i = 0; i < BINS; i++) {
		flat[i] = 0.5f;
		spike[i] = 0.0f;
		zero[i] = 0.0f;
	}
	spike[10] = 1.0f;

	/* Flat detail: monotonic, starts near 0, ends near 1. */
	cw_build_map(flat, BINS, P, EW, SRC_W, map, MAP_W);
	int mono = 1;
	for (int x = 1; x < MAP_W; x++)
		if (map[x] < map[x - 1])
			mono = 0;
	CHECK(mono, "flat map is monotonic");
	CHECK(map[0] < 0.01f, "map starts at the left edge");
	CHECK(map[MAP_W - 1] > 0.99f, "map ends at the right edge");

	/* Centre is 1:1: one output pixel is 1/srcW of the source. */
	float eL = (EW - P * SRC_W) * 0.5f;
	int c0 = (int)ceilf(eL), c1 = (int)floorf(EW - eL) - 2;
	int centre_ok = 1;
	for (int x = c0; x < c1; x++)
		if (fabsf((map[x + 1] - map[x]) - 1.0f / SRC_W) > 1e-4f)
			centre_ok = 0;
	CHECK(centre_ok, "centre slope is 1:1");

	/* No detail: even spread on each side (same as flat). */
	float map_zero[MAP_W];
	cw_build_map(zero, BINS, P, EW, SRC_W, map_zero, MAP_W);
	CHECK(fabsf(map_zero[5] - map[5]) < 1e-4f, "zero detail matches flat detail");

	/* A detailed bin gets more output pixels than a flat bin. */
	cw_build_map(spike, BINS, P, EW, SRC_W, map, MAP_W);
	float share_spike = left_share(map, 10);
	float share_flat = left_share(map_zero, 10);
	CHECK(share_spike > share_flat * 2.0f, "detailed bin gets more output pixels");

	if (fails) {
		fprintf(stderr, "%d test(s) failed\n", fails);
		return 1;
	}
	printf("content-warp warp math: PASS\n");
	return 0;
}
