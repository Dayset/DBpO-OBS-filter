#pragma once

/* Color core for perceptual color reduction. Pure C, no OBS dependency, so the
 * same code runs in the plugin, the unit tests and the bitrate harness.
 *
 * Colors are in OKLab (Ottosson 2020): L in 0..1, a/b roughly -0.3..0.3.
 * OKLab is used instead of CIELAB because it is more perceptually uniform in
 * hue and cheaper to convert to/from on the GPU. */

#include <stdbool.h>
#include <stddef.h>

#define PALETTE_MAX 256

struct palette {
	int n;
	float c[PALETTE_MAX][3]; /* OKLab */
};

void srgb_to_oklab(const float rgb[3], float lab[3]);
void oklab_to_srgb(const float lab[3], float rgb[3]);

/* Weighted squared distance; luma_weight > 1 protects lightness detail. */
static inline float oklab_dist2(const float x[3], const float y[3], float luma_weight)
{
	const float dl = (x[0] - y[0]) * luma_weight, da = x[1] - y[1], db = x[2] - y[2];
	return dl * dl + da * da + db * db;
}

/* Octree quantization (Gervautz & Purgathofer) in OKLab: deterministic,
 * merges the least-populated deepest nodes first. Returns colors produced. */
int palette_octree(const float *lab, size_t n, int k, struct palette *out);

/* K-means (Lloyd) on the samples. If `init` has k colors it is used as the
 * starting point (warm start: stable from frame to frame); otherwise
 * deterministic k-means++ seeding. */
void palette_kmeans(const float *lab, size_t n, int k, int iterations, float luma_weight, const struct palette *init,
		    struct palette *out);

/* Reorders `fresh` so each entry lines up with the nearest entry of `prev`
 * (greedy matching), then moves each matched entry only part of the way:
 * fresh = prev + (fresh - prev) * (1 - smoothing). Unmatched entries are
 * kept as is. Prevents palette shimmer between rebuilds. */
void palette_stabilize(const struct palette *prev, struct palette *fresh, float smoothing, float luma_weight);

/* Mean distance from each sample to its nearest palette color. */
float palette_mean_error(const float *lab, size_t n, const struct palette *p, float luma_weight);
