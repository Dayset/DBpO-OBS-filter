/*
Content Warp: CPU warp map. Same maths as the old PSMap / src_u shader pass.
*/
#include "warp-math.h"

#include <math.h>

#define CW_EPS 0.02f

/* Inverse cumulative detail over bins [lo, lo + n): source position in [0, 1] for t in [0, 1]. */
static float side_pos(const float *energy, int bins, float t, int lo, int n)
{
	if (n <= 0)
		return (float)lo / (float)bins;

	float tot = 0.0f;
	for (int i = lo; i < lo + n; i++)
		tot += energy[i] + CW_EPS;

	float target = t * tot;
	float acc = 0.0f;
	for (int k = lo; k < lo + n; k++) {
		float w = energy[k] + CW_EPS;
		if (acc + w >= target)
			return (k + (target - acc) / w) / (float)bins;
		acc += w;
	}
	return (float)(lo + n) / (float)bins;
}

void cw_build_map(const float *energy, int bins, float p, float ew, float srcW, float *map, int map_w)
{
	float L = 0.5f - p * 0.5f;
	float R = 0.5f + p * 0.5f;
	float eL = (ew - p * srcW) * 0.5f; /* width of each side in output pixels */
	int nL = (int)floorf(L * (float)bins);
	int n0 = (int)ceilf(R * (float)bins);

	for (int x = 0; x < map_w; x++) {
		float xE = (float)x + 0.5f;
		float u;
		if (eL <= 0.0f) {
			u = xE / ew;
		} else if (xE < eL) {
			u = side_pos(energy, bins, xE / eL, 0, nL);
		} else if (xE >= ew - eL) {
			u = side_pos(energy, bins, (xE - (ew - eL)) / eL, n0, bins - n0);
		} else {
			u = L + (xE - eL) / srcW;
		}
		map[x] = u;
	}
}
