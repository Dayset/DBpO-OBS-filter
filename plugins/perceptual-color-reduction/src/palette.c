/*
Perceptual Color Reduction
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

#include "palette.h"

#include <float.h>
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* ------------------------------------------------------------------ OKLab */

static float srgb_to_linear(float c)
{
	return c <= 0.04045f ? c / 12.92f : powf((c + 0.055f) / 1.055f, 2.4f);
}

static float linear_to_srgb(float c)
{
	c = c < 0.0f ? 0.0f : (c > 1.0f ? 1.0f : c);
	return c <= 0.0031308f ? c * 12.92f : 1.055f * powf(c, 1.0f / 2.4f) - 0.055f;
}

void srgb_to_oklab(const float rgb[3], float lab[3])
{
	const float r = srgb_to_linear(rgb[0]), g = srgb_to_linear(rgb[1]), b = srgb_to_linear(rgb[2]);
	const float l = cbrtf(0.4122214708f * r + 0.5363325363f * g + 0.0514459929f * b);
	const float m = cbrtf(0.2119034982f * r + 0.6806995451f * g + 0.1073969566f * b);
	const float s = cbrtf(0.0883024619f * r + 0.2817188376f * g + 0.6299787005f * b);
	lab[0] = 0.2104542553f * l + 0.7936177850f * m - 0.0040720468f * s;
	lab[1] = 1.9779984951f * l - 2.4285922050f * m + 0.4505937099f * s;
	lab[2] = 0.0259040371f * l + 0.7827717662f * m - 0.8086757660f * s;
}

void oklab_to_srgb(const float lab[3], float rgb[3])
{
	const float l_ = lab[0] + 0.3963377774f * lab[1] + 0.2158037573f * lab[2];
	const float m_ = lab[0] - 0.1055613458f * lab[1] - 0.0638541728f * lab[2];
	const float s_ = lab[0] - 0.0894841775f * lab[1] - 1.2914855480f * lab[2];
	const float l = l_ * l_ * l_, m = m_ * m_ * m_, s = s_ * s_ * s_;
	rgb[0] = linear_to_srgb(4.0767416621f * l - 3.3077115913f * m + 0.2309699292f * s);
	rgb[1] = linear_to_srgb(-1.2684380046f * l + 2.6097574011f * m - 0.3413193965f * s);
	rgb[2] = linear_to_srgb(-0.0041960863f * l - 0.7034186147f * m + 1.7076147010f * s);
}

/* ------------------------------------------------------------------ octree */

#define OCT_DEPTH 6 /* 64 bins per axis is far finer than any palette we build */
#define AB_RANGE 0.32f

struct oct_node {
	int child[8];
	int parent;
	int level;
	uint32_t count;
	double sum[3];
	bool leaf;
	bool alive;
};

struct octree {
	struct oct_node *nodes;
	int used, cap;
	int leaves;
};

static int oct_new(struct octree *t, int parent, int level)
{
	if (t->used == t->cap) {
		int cap = t->cap ? t->cap * 2 : 1024;
		struct oct_node *n = realloc(t->nodes, (size_t)cap * sizeof(*n));
		if (!n)
			return -1;
		t->nodes = n;
		t->cap = cap;
	}
	struct oct_node *n = &t->nodes[t->used];
	memset(n, 0, sizeof(*n));
	for (int i = 0; i < 8; i++)
		n->child[i] = -1;
	n->parent = parent;
	n->level = level;
	n->leaf = level == OCT_DEPTH;
	n->alive = true;
	if (n->leaf)
		t->leaves++;
	return t->used++;
}

static int quant8(float v, float lo, float hi)
{
	int q = (int)((v - lo) / (hi - lo) * 255.0f + 0.5f);
	return q < 0 ? 0 : (q > 255 ? 255 : q);
}

static void oct_insert(struct octree *t, const float lab[3])
{
	const int q[3] = {quant8(lab[0], 0.0f, 1.0f), quant8(lab[1], -AB_RANGE, AB_RANGE),
			  quant8(lab[2], -AB_RANGE, AB_RANGE)};
	int node = 0;
	for (int level = 0; level < OCT_DEPTH; level++) {
		const int shift = 7 - level;
		const int idx = (((q[0] >> shift) & 1) << 2) | (((q[1] >> shift) & 1) << 1) | ((q[2] >> shift) & 1);
		if (t->nodes[node].child[idx] < 0) {
			int c = oct_new(t, node, level + 1);
			if (c < 0)
				return;
			t->nodes[node].child[idx] = c;
		}
		node = t->nodes[node].child[idx];
	}
	struct oct_node *leaf = &t->nodes[node];
	leaf->count++;
	for (int i = 0; i < 3; i++)
		leaf->sum[i] += lab[i];
}

/* Merges all children of `node` into it (they must be leaves). */
static void oct_merge(struct octree *t, int node)
{
	struct oct_node *n = &t->nodes[node];
	for (int i = 0; i < 8; i++) {
		int c = n->child[i];
		if (c < 0)
			continue;
		struct oct_node *ch = &t->nodes[c];
		n->count += ch->count;
		for (int j = 0; j < 3; j++)
			n->sum[j] += ch->sum[j];
		ch->alive = false;
		t->leaves--;
		n->child[i] = -1;
	}
	n->leaf = true;
	t->leaves++;
}

struct merge_candidate {
	uint32_t count;
	int node;
};

static int cmp_candidate(const void *x, const void *y)
{
	const struct merge_candidate *a = x, *b = y;
	if (a->count != b->count)
		return a->count < b->count ? -1 : 1;
	return (a->node > b->node) - (a->node < b->node);
}

int palette_octree(const float *lab, size_t n, int k, struct palette *out)
{
	out->n = 0;
	if (k < 1)
		return 0;
	if (k > PALETTE_MAX)
		k = PALETTE_MAX;

	struct octree t = {0};
	if (oct_new(&t, -1, 0) < 0)
		return 0;
	for (size_t i = 0; i < n; i++)
		oct_insert(&t, lab + i * 3);

	/* Reduce: deepest level first. All nodes on the level being reduced have
	 * only leaf children (deeper levels are already merged), so their sample
	 * counts are fixed: sort once, merge the least-populated first (lowest
	 * index on ties, so results are deterministic). */
	struct merge_candidate *cand = malloc((size_t)t.used * sizeof(*cand));
	for (int level = OCT_DEPTH - 1; cand && level >= 0 && t.leaves > k; level--) {
		int nc = 0;
		for (int i = 0; i < t.used; i++) {
			struct oct_node *nd = &t.nodes[i];
			if (!nd->alive || nd->leaf || nd->level != level)
				continue;
			uint32_t cnt = 0;
			for (int c = 0; c < 8; c++)
				if (nd->child[c] >= 0)
					cnt += t.nodes[nd->child[c]].count;
			cand[nc].count = cnt;
			cand[nc].node = i;
			nc++;
		}
		qsort(cand, (size_t)nc, sizeof(*cand), cmp_candidate);
		for (int i = 0; i < nc && t.leaves > k; i++)
			oct_merge(&t, cand[i].node);
	}
	free(cand);

	for (int i = 0; i < t.used && out->n < k; i++) {
		struct oct_node *nd = &t.nodes[i];
		if (!nd->alive || !nd->leaf || nd->count == 0)
			continue;
		for (int j = 0; j < 3; j++)
			out->c[out->n][j] = (float)(nd->sum[j] / nd->count);
		out->n++;
	}
	free(t.nodes);
	return out->n;
}

/* ------------------------------------------------------------------ k-means */

static int nearest(const struct palette *p, const float x[3], float luma_weight, float *dist2)
{
	int best = 0;
	float bd = FLT_MAX;
	for (int i = 0; i < p->n; i++) {
		float d = oklab_dist2(x, p->c[i], luma_weight);
		if (d < bd) {
			bd = d;
			best = i;
		}
	}
	if (dist2)
		*dist2 = bd;
	return best;
}

/* Deterministic k-means++: first center = sample closest to the mean, then
 * each next center is the sample farthest (weighted) from existing ones. */
static void kmeans_seed(const float *lab, size_t n, int k, float luma_weight, struct palette *out)
{
	out->n = 0;
	if (n == 0)
		return;
	double mean[3] = {0};
	for (size_t i = 0; i < n; i++)
		for (int j = 0; j < 3; j++)
			mean[j] += lab[i * 3 + j];
	float m[3] = {(float)(mean[0] / n), (float)(mean[1] / n), (float)(mean[2] / n)};
	size_t first = 0;
	float fd = FLT_MAX;
	for (size_t i = 0; i < n; i++) {
		float d = oklab_dist2(lab + i * 3, m, luma_weight);
		if (d < fd) {
			fd = d;
			first = i;
		}
	}
	float *d2 = malloc(n * sizeof(float));
	if (!d2)
		return;
	memcpy(out->c[0], lab + first * 3, sizeof(out->c[0]));
	out->n = 1;
	for (size_t i = 0; i < n; i++)
		d2[i] = oklab_dist2(lab + i * 3, out->c[0], luma_weight);
	while (out->n < k) {
		size_t far = 0;
		float fdist = -1.0f;
		for (size_t i = 0; i < n; i++)
			if (d2[i] > fdist) {
				fdist = d2[i];
				far = i;
			}
		if (fdist <= 0.0f)
			break; /* fewer distinct colors than k */
		memcpy(out->c[out->n], lab + far * 3, sizeof(out->c[0]));
		for (size_t i = 0; i < n; i++) {
			float d = oklab_dist2(lab + i * 3, out->c[out->n], luma_weight);
			if (d < d2[i])
				d2[i] = d;
		}
		out->n++;
	}
	free(d2);
}

void palette_kmeans(const float *lab, size_t n, int k, int iterations, float luma_weight, const struct palette *init,
		    struct palette *out)
{
	if (k > PALETTE_MAX)
		k = PALETTE_MAX;
	if (init && init->n == k && init != out)
		*out = *init;
	else if (!(init == out && out->n == k))
		kmeans_seed(lab, n, k, luma_weight, out);
	if (out->n == 0)
		return;

	double(*sum)[3] = calloc((size_t)out->n, sizeof(*sum));
	size_t *cnt = calloc((size_t)out->n, sizeof(*cnt));
	if (!sum || !cnt) {
		free(sum);
		free(cnt);
		return;
	}
	for (int it = 0; it < iterations; it++) {
		memset(sum, 0, (size_t)out->n * sizeof(*sum));
		memset(cnt, 0, (size_t)out->n * sizeof(*cnt));
		for (size_t i = 0; i < n; i++) {
			int c = nearest(out, lab + i * 3, luma_weight, NULL);
			for (int j = 0; j < 3; j++)
				sum[c][j] += lab[i * 3 + j];
			cnt[c]++;
		}
		for (int c = 0; c < out->n; c++) {
			if (!cnt[c])
				continue; /* empty cluster keeps its color: stable over time */
			for (int j = 0; j < 3; j++)
				out->c[c][j] = (float)(sum[c][j] / cnt[c]);
		}
	}
	free(sum);
	free(cnt);
}

/* ------------------------------------------------------------------ stability */

void palette_stabilize(const struct palette *prev, struct palette *fresh, float smoothing, float luma_weight)
{
	if (!prev || prev->n == 0 || fresh->n == 0)
		return;
	if (smoothing < 0.0f)
		smoothing = 0.0f;
	if (smoothing > 0.99f)
		smoothing = 0.99f;

	const int n = fresh->n;
	bool used_prev[PALETTE_MAX] = {false};
	bool used_fresh[PALETTE_MAX] = {false};
	int match[PALETTE_MAX];
	for (int i = 0; i < n; i++)
		match[i] = -1;

	/* Greedy global matching: repeatedly take the closest unmatched pair. */
	const int pairs = n < prev->n ? n : prev->n;
	for (int m = 0; m < pairs; m++) {
		float best = FLT_MAX;
		int bi = -1, bj = -1;
		for (int i = 0; i < n; i++) {
			if (used_fresh[i])
				continue;
			for (int j = 0; j < prev->n; j++) {
				if (used_prev[j])
					continue;
				float d = oklab_dist2(fresh->c[i], prev->c[j], luma_weight);
				if (d < best) {
					best = d;
					bi = i;
					bj = j;
				}
			}
		}
		if (bi < 0)
			break;
		used_fresh[bi] = used_prev[bj] = true;
		match[bi] = bj;
	}

	struct palette out = {.n = 0};
	/* Matched entries keep the previous slot order and move partway. */
	for (int j = 0; j < prev->n; j++) {
		for (int i = 0; i < n; i++) {
			if (match[i] != j)
				continue;
			for (int c = 0; c < 3; c++)
				out.c[out.n][c] = prev->c[j][c] + (fresh->c[i][c] - prev->c[j][c]) * (1.0f - smoothing);
			out.n++;
		}
	}
	for (int i = 0; i < n; i++)
		if (match[i] < 0)
			memcpy(out.c[out.n++], fresh->c[i], sizeof(out.c[0]));
	*fresh = out;
}

float palette_mean_error(const float *lab, size_t n, const struct palette *p, float luma_weight)
{
	if (!n || !p->n)
		return 0.0f;
	double acc = 0.0;
	for (size_t i = 0; i < n; i++) {
		float d2;
		nearest(p, lab + i * 3, luma_weight, &d2);
		acc += sqrt((double)d2);
	}
	return (float)(acc / (double)n);
}
