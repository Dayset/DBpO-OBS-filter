/*
Directional Object Blur
Copyright (C) 2026 dayset <dayset@gmail.com>

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License along
with this program. If not, see <https://www.gnu.org/licenses/>
*/

#include "camera-model.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#define MAX_SAMPLES 4096
#define MIN_SAMPLES 12
#define IRLS_ITERATIONS 5

/* Solves the symmetric 3x3 system A * s = b. Returns false if singular. */
static bool solve3(const double A[3][3], const double b[3], double s[3])
{
	double det = A[0][0] * (A[1][1] * A[2][2] - A[1][2] * A[2][1]) -
		     A[0][1] * (A[1][0] * A[2][2] - A[1][2] * A[2][0]) +
		     A[0][2] * (A[1][0] * A[2][1] - A[1][1] * A[2][0]);
	if (fabs(det) < 1e-12)
		return false;

	for (int c = 0; c < 3; c++) {
		double M[3][3];
		memcpy(M, A, sizeof(M));
		for (int r = 0; r < 3; r++)
			M[r][c] = b[r];
		s[c] = (M[0][0] * (M[1][1] * M[2][2] - M[1][2] * M[2][1]) -
			M[0][1] * (M[1][0] * M[2][2] - M[1][2] * M[2][0]) +
			M[0][2] * (M[1][0] * M[2][1] - M[1][1] * M[2][0])) /
		       det;
	}
	return true;
}

static int cmp_float(const void *a, const void *b)
{
	float fa = *(const float *)a, fb = *(const float *)b;
	return (fa > fb) - (fa < fb);
}

struct sample {
	float px, py; /* normalized position, -0.5..0.5 */
	float vx, vy; /* measured motion, px */
	float conf;
};

static bool weighted_fit(const struct sample *s, const float *w, size_t n, struct camera_model *m)
{
	double A[3][3] = {{0}}, bx[3] = {0}, by[3] = {0};
	double wsum = 0.0;

	for (size_t i = 0; i < n; i++) {
		if (w[i] <= 0.0f)
			continue;
		const double phi[3] = {1.0, s[i].px, s[i].py};
		for (int r = 0; r < 3; r++) {
			for (int c = 0; c < 3; c++)
				A[r][c] += w[i] * phi[r] * phi[c];
			bx[r] += w[i] * phi[r] * s[i].vx;
			by[r] += w[i] * phi[r] * s[i].vy;
		}
		wsum += w[i];
	}
	if (wsum <= 0.0)
		return false;

	/* Tiny ridge keeps zoom/rotation terms at zero when samples are
	 * clustered (e.g. only a strip of the frame is textured). */
	A[1][1] += 1e-4 * wsum;
	A[2][2] += 1e-4 * wsum;

	double sx[3], sy[3];
	if (!solve3(A, bx, sx) || !solve3(A, by, sy))
		return false;
	for (int i = 0; i < 3; i++) {
		m->x[i] = (float)sx[i];
		m->y[i] = (float)sy[i];
	}
	return true;
}

static void model_eval(const struct camera_model *m, float px, float py, float *vx, float *vy)
{
	*vx = m->x[0] + m->x[1] * px + m->x[2] * py;
	*vy = m->y[0] + m->y[1] * px + m->y[2] * py;
}

bool camera_model_fit(const float *rgba, size_t w, size_t h, size_t stride, float min_confidence,
		      struct camera_model *out)
{
	static struct sample samples[MAX_SAMPLES];
	static float weights[MAX_SAMPLES];
	static float residuals[MAX_SAMPLES];
	size_t n = 0;

	memset(out, 0, sizeof(*out));

	for (size_t y = 0; y < h; y++) {
		const float *row = rgba + y * stride;
		for (size_t x = 0; x < w && n < MAX_SAMPLES; x++) {
			const float *p = row + x * 4;
			if (!isfinite(p[0]) || !isfinite(p[1]) || !(p[2] >= min_confidence))
				continue;
			samples[n].px = ((float)x + 0.5f) / (float)w - 0.5f;
			samples[n].py = ((float)y + 0.5f) / (float)h - 0.5f;
			samples[n].vx = p[0];
			samples[n].vy = p[1];
			samples[n].conf = p[2];
			n++;
		}
	}
	if (n < MIN_SAMPLES)
		return false;

	for (size_t i = 0; i < n; i++)
		weights[i] = samples[i].conf;

	struct camera_model m;
	if (!weighted_fit(samples, weights, n, &m))
		return false;

	for (int iter = 0; iter < IRLS_ITERATIONS; iter++) {
		for (size_t i = 0; i < n; i++) {
			float ex, ey;
			model_eval(&m, samples[i].px, samples[i].py, &ex, &ey);
			residuals[i] = hypotf(samples[i].vx - ex, samples[i].vy - ey);
		}

		/* Robust scale from the median residual (MAD-style), floored at
		 * one pixel so block-matching quantization isn't treated as noise. */
		memcpy(weights, residuals, n * sizeof(float));
		qsort(weights, n, sizeof(float), cmp_float);
		float scale = 1.4826f * weights[n / 2];
		if (scale < 1.0f)
			scale = 1.0f;
		const float c = 4.685f * scale;

		size_t inliers = 0;
		for (size_t i = 0; i < n; i++) {
			float r = residuals[i] / c;
			float t = r < 1.0f ? (1.0f - r * r) : 0.0f;
			weights[i] = samples[i].conf * t * t;
			inliers += weights[i] > 0.0f;
		}
		if (inliers < MIN_SAMPLES)
			break;

		struct camera_model next;
		if (!weighted_fit(samples, weights, n, &next))
			break;
		m = next;
	}

	*out = m;
	return true;
}

void camera_model_smooth(struct camera_model *previous, const struct camera_model *fresh, float smoothing)
{
	if (smoothing < 0.0f)
		smoothing = 0.0f;
	if (smoothing > 0.99f)
		smoothing = 0.99f;
	for (int i = 0; i < 3; i++) {
		previous->x[i] = fresh->x[i] + (previous->x[i] - fresh->x[i]) * smoothing;
		previous->y[i] = fresh->y[i] + (previous->y[i] - fresh->y[i]) * smoothing;
	}
}

float camera_match_score(const float *rgba, size_t w, size_t h, size_t stride)
{
	size_t good = 0, total = 0;
	for (size_t y = 0; y < h; y++) {
		const float *row = rgba + y * stride;
		for (size_t x = 0; x < w; x++) {
			float q = row[x * 4 + 3];
			if (!isfinite(q))
				continue;
			good += q > 0.5f;
			total++;
		}
	}
	return total ? (float)good / (float)total : 0.0f;
}
