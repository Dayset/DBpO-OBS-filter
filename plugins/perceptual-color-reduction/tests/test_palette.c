#define _POSIX_C_SOURCE 199309L
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "palette.h"

#define NS 14400 /* 160 x 90 samples, what the plugin reads back */

static float lab[NS * 3];
static int failures;

static void check(int ok, const char *what)
{
	printf("%-58s %s\n", what, ok ? "PASS" : "FAIL");
	failures += !ok;
}

static float frand(void)
{
	return (float)rand() / (float)RAND_MAX;
}

static double now_ms(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
}

/* Synthetic "frame": smooth gradients + a few flat colored regions + noise. */
static void make_frame(float noise, unsigned seed)
{
	srand(seed);
	for (int i = 0; i < NS; i++) {
		int x = i % 160, y = i / 160;
		float rgb[3];
		if (y < 30) { /* sky gradient */
			rgb[0] = 0.35f + 0.003f * y;
			rgb[1] = 0.55f + 0.004f * y;
			rgb[2] = 0.85f - 0.002f * y;
		} else if (x < 50) { /* grass */
			rgb[0] = 0.20f;
			rgb[1] = 0.45f + 0.1f * sinf(x * 0.3f);
			rgb[2] = 0.15f;
		} else if (x < 110) { /* rock */
			rgb[0] = rgb[1] = rgb[2] = 0.35f + 0.15f * sinf(x * 0.11f + y * 0.07f);
		} else { /* red object */
			rgb[0] = 0.8f;
			rgb[1] = 0.12f;
			rgb[2] = 0.1f;
		}
		for (int c = 0; c < 3; c++) {
			rgb[c] += (frand() - 0.5f) * noise;
			rgb[c] = rgb[c] < 0 ? 0 : (rgb[c] > 1 ? 1 : rgb[c]);
		}
		srgb_to_oklab(rgb, lab + i * 3);
	}
}

static float max_shift(const struct palette *a, const struct palette *b)
{
	float m = 0;
	int n = a->n < b->n ? a->n : b->n;
	for (int i = 0; i < n; i++) {
		float d = sqrtf(oklab_dist2(a->c[i], b->c[i], 1.0f));
		if (d > m)
			m = d;
	}
	return m;
}

/* What viewers see: mean change of each sample's output color when the
 * palette changes from a to b (same input). */
static float output_shimmer(const struct palette *a, const struct palette *b)
{
	double acc = 0;
	for (int i = 0; i < NS; i++) {
		const float *x = lab + i * 3;
		int ia = 0, ib = 0;
		float da = 1e9f, db = 1e9f;
		for (int j = 0; j < a->n; j++) {
			float d = oklab_dist2(x, a->c[j], 1.0f);
			if (d < da) {
				da = d;
				ia = j;
			}
		}
		for (int j = 0; j < b->n; j++) {
			float d = oklab_dist2(x, b->c[j], 1.0f);
			if (d < db) {
				db = d;
				ib = j;
			}
		}
		acc += sqrtf(oklab_dist2(a->c[ia], b->c[ib], 1.0f));
	}
	return (float)(acc / NS);
}

int main(void)
{
	/* OKLab conversion */
	float worst = 0, lab1[3], back[3];
	srand(1);
	for (int i = 0; i < 100000; i++) {
		float rgb[3] = {frand(), frand(), frand()};
		srgb_to_oklab(rgb, lab1);
		oklab_to_srgb(lab1, back);
		for (int c = 0; c < 3; c++)
			worst = fmaxf(worst, fabsf(back[c] - rgb[c]));
	}
	printf("OKLab round trip worst error: %.2e (8-bit step is 3.9e-3)\n", worst);
	check(worst < 1e-4f, "OKLab round trip is lossless at 8-bit precision");
	float white[3] = {1, 1, 1}, wl[3];
	srgb_to_oklab(white, wl);
	check(fabsf(wl[0] - 1) < 1e-3f && fabsf(wl[1]) < 1e-3f && fabsf(wl[2]) < 1e-3f, "white is L=1, a=b=0");

	/* Octree recovers a small set of distinct colors exactly */
	const float base[8][3] = {{0, 0, 0}, {1, 1, 1}, {1, 0, 0},          {0, 1, 0},
				  {0, 0, 1}, {1, 1, 0}, {0.5f, 0.5f, 0.5f}, {0.2f, 0.4f, 0.6f}};
	for (int i = 0; i < NS; i++)
		srgb_to_oklab(base[i % 8], lab + i * 3);
	struct palette p;
	int n = palette_octree(lab, NS, 8, &p);
	float err = palette_mean_error(lab, NS, &p, 1.0f);
	printf("octree on 8 distinct colors: %d colors, mean error %.4f\n", n, err);
	check(n == 8 && err < 0.01f, "octree recovers 8 distinct colors");

	/* Quality and speed on a realistic frame */
	make_frame(0.04f, 2);
	double t0 = now_ms();
	n = palette_octree(lab, NS, 128, &p);
	double t_oct = now_ms() - t0;
	float e_oct = palette_mean_error(lab, NS, &p, 1.0f);
	struct palette km = {0};
	t0 = now_ms();
	palette_kmeans(lab, NS, 128, 4, 1.0f, NULL, &km);
	double t_km = now_ms() - t0;
	float e_km = palette_mean_error(lab, NS, &km, 1.0f);
	printf("128 colors, %d samples: octree %d colors err %.4f in %.1f ms | k-means err %.4f in %.1f ms (cold)\n",
	       NS, n, e_oct, t_oct, e_km, t_km);
	check(n > 64 && n <= 128 && e_oct < 0.03f, "octree: <=128 colors, mean OKLab error < 0.03");
	check(e_km <= e_oct * 1.05f, "k-means at least as accurate as octree");
	check(t_oct < 20.0, "octree rebuild fast enough for the render thread (< 20 ms)");

	/* Warm-started k-means: one iteration per frame is cheap and stable */
	struct palette km_prev = km;
	make_frame(0.04f, 3); /* same scene, new noise */
	t0 = now_ms();
	palette_kmeans(lab, NS, 128, 1, 1.0f, &km, &km);
	double t_warm = now_ms() - t0;
	float km_shift = max_shift(&km_prev, &km);
	printf("k-means warm update: %.1f ms, max palette shift %.4f\n", t_warm, km_shift);
	check(t_warm < 15.0, "warm k-means update < 15 ms");

	/* Octree rebuilt from scratch on the new noise, with and without
	 * stabilization, vs warm k-means: how much does the picture shimmer? */
	struct palette oct_prev = p, oct_new, oct_stab;
	palette_octree(lab, NS, 128, &oct_new);
	oct_stab = oct_new;
	palette_stabilize(&oct_prev, &oct_stab, 0.8f, 1.0f);
	float sh_oct = output_shimmer(&oct_prev, &oct_new);
	float sh_stab = output_shimmer(&oct_prev, &oct_stab);
	float sh_km = output_shimmer(&km_prev, &km);
	printf("output shimmer on a noise-only change (mean OKLab): octree rebuild %.4f, octree+stabilize(0.8) %.4f, "
	       "warm k-means %.4f\n",
	       sh_oct, sh_stab, sh_km);
	check(sh_stab < sh_oct * 0.6f, "stabilize cuts octree shimmer substantially");
	/* ~0.02 OKLab is a just-noticeable difference; stay well under a tenth. */
	check(sh_km < sh_oct && sh_stab < 0.002f && sh_km < 0.002f, "palette updates shimmer < 1/10 of a JND");
	check(km_shift < 0.05f, "warm k-means palette barely moves on noise-only change");

	printf(failures ? "SOME FAILED\n" : "ALL PASS\n");
	return failures != 0;
}
