/* dbpo-squish: Foveated Squish over yuv4mpeg 4:2:0 8-bit pipes.
 *   dbpo-squish [-u] [-p] [-t] W H < in.y4m > out.y4m
 * W H: output size (squish) or the original size (-u, input is the squished stream).
 * -p point-sampled (one bilinear tap per output pixel); default is area-filtered. -u unsquish (bilinear). -t timing.
 * The mapping comes from plugins/foveated-squish/src/squish-math.c, centre 30%, linear, focus 0.5. */
#include "../../plugins/foveated-squish/src/squish-math.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define CENTER 0.15 /* half-size of the sharp centre */
struct taps { int n, *s, *c, *run; int16_t *w; int max; }; /* out n; per output: first source, count, Q14 weights */

static void axis(struct taps *t, int in, int out, int mode) { /* mode 0 area, 1 point, 2 unsquish (in = squished) */
	int sq = mode == 2 ? in : out, src = mode == 2 ? out : in;
	struct squish_axis a = {(double)sq / src, CENTER, 0.5, 1.0};
	t->n = out; t->s = malloc(out * sizeof(int)); t->c = malloc(out * sizeof(int));
	t->max = mode == 0 ? src / sq + 3 : 2; t->w = calloc((size_t)out * t->max, sizeof(int16_t));
	for (int j = 0; j < out; j++) {
		int16_t *w = t->w + (size_t)j * t->max;
		if (mode == 0) { /* overlap of squished pixel j with source pixels, from the forward map of the source edges */
			double lo = squish_inverse(&a, (double)j / out) * in, hi = squish_inverse(&a, (double)(j + 1) / out) * in;
			int i0 = (int)lo, i1 = (int)hi; if (i1 >= in) i1 = in - 1;
			double sum = 0, ov[64]; int n = 0;
			for (int i = i0; i <= i1 && n < t->max; i++, n++) {
				double l = i > lo ? i : lo, h = i + 1 < hi ? i + 1 : hi; ov[n] = h > l ? h - l : 0; sum += ov[n];
			}
			t->s[j] = i0; t->c[j] = n; int acc = 0;
			for (int i = 0; i < n; i++) { w[i] = (int16_t)(ov[i] / sum * 16384 + 0.5); acc += w[i]; }
			w[n / 2] += 16384 - acc;
		} else { /* position in the other image of this pixel's centre; bilinear */
			double x = mode == 1 ? squish_inverse(&a, (j + 0.5) / out) * in - 0.5 : squish_forward(&a, (j + 0.5) / out) * in - 0.5;
			x = x < 0 ? 0 : (x > in - 1 ? in - 1 : x);
			int i = (int)x; if (i > in - 2) i = in - 2; if (i < 0) i = 0;
			int f = (int)((x - i) * 16384 + 0.5); t->s[j] = i; t->c[j] = in > 1 ? 2 : 1;
			w[0] = 16384 - f; w[1] = f;
		}
	}
	t->run = calloc(out, sizeof(int)); /* run[j]: length of the stretch from j with equal weights and unit step (the sharp centre) */
	for (int j = out - 1; j >= 0; j--) {
		const int16_t *w = t->w + (size_t)j * t->max;
		int same = j + 1 < out && t->c[j + 1] == t->c[j] && t->s[j + 1] == t->s[j] + 1 && !memcmp(w, w + t->max, t->max * sizeof(int16_t));
		t->run[j] = same ? t->run[j + 1] + 1 : 1;
	}
}

static void plane(const uint8_t *in, int iw, int ih, uint8_t *out, int ow, int oh, const struct taps *X, const struct taps *Y, uint8_t *mid) {
	(void)ih; /* vertical pass first: it vectorises along the row and leaves fewer rows for the horizontal pass */
	for (int y = 0; y < oh; y++) {
		const int16_t *w = Y->w + (size_t)y * Y->max; const uint8_t *r = in + (size_t)Y->s[y] * iw; uint8_t *o = mid + (size_t)y * iw;
		const int c = Y->c[y];
		if (c == 1) { memcpy(o, r, iw); continue; }
		const uint8_t *r1 = r + iw, *r2 = r + 2 * iw; /* fixed tap counts so the loop vectorises */
		if (c == 2) for (int x = 0; x < iw; x++) o[x] = (uint8_t)((8192 + w[0] * r[x] + w[1] * r1[x]) >> 14);
		else if (c == 3) for (int x = 0; x < iw; x++) o[x] = (uint8_t)((8192 + w[0] * r[x] + w[1] * r1[x] + w[2] * r2[x]) >> 14);
		else for (int x = 0; x < iw; x++) {
			int a = 8192; for (int k = 0; k < c; k++) a += w[k] * r[(size_t)k * iw + x];
			o[x] = (uint8_t)(a >> 14);
		}
	}
	for (int y = 0; y < oh; y++) {
		const uint8_t *r = mid + (size_t)y * iw; uint8_t *o = out + (size_t)y * ow;
		for (int x = 0; x < ow;) {
			const int16_t *w = X->w + (size_t)x * X->max; const uint8_t *p = r + X->s[x]; const int c = X->c[x], len = X->run[x];
			if (len >= 16) { /* constant weights: vectorises across x */
				for (int i = 0; i < len; i++) { int a = 8192; for (int k = 0; k < c; k++) a += w[k] * p[i + k]; o[x + i] = (uint8_t)(a >> 14); }
				x += len; continue;
			}
			int a = 8192; for (int k = 0; k < c; k++) a += w[k] * p[k];
			o[x++] = (uint8_t)(a >> 14);
		}
	}
}

int main(int argc, char **argv) {
	int mode = 0, timing = 0, n = 0, dim[2];
	for (int i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "-u")) mode = 2; else if (!strcmp(argv[i], "-p")) mode = 1; else if (!strcmp(argv[i], "-t")) timing = 1;
		else if (n < 2) dim[n++] = atoi(argv[i]);
	}
	if (n < 2) { fprintf(stderr, "usage: dbpo-squish [-u] [-p] [-t] W H < in.y4m > out.y4m\n"); return 2; }
	char line[512]; int iw = 0, ih = 0;
	if (!fgets(line, sizeof line, stdin)) return 1;
	for (char *t = strtok(line, " \n"); t; t = strtok(NULL, " \n")) {
		if (*t == 'W') iw = atoi(t + 1); else if (*t == 'H') ih = atoi(t + 1);
		else if (*t == 'C' && strncmp(t + 1, "420", 3)) { fprintf(stderr, "only 4:2:0 is supported\n"); return 1; }
	}
	int ow = dim[0], oh = dim[1];
	printf("YUV4MPEG2 W%d H%d F60:1 Ip A1:1 C420jpeg\n", ow, oh);
	struct taps X[2], Y[2]; int d = mode == 2;
	axis(&X[0], iw, ow, mode); axis(&Y[0], ih, oh, mode); axis(&X[1], iw / 2, ow / 2, mode); axis(&Y[1], ih / 2, oh / 2, mode);
	size_t isz = (size_t)iw * ih * 3 / 2, osz = (size_t)ow * oh * 3 / 2;
	uint8_t *in = malloc(isz), *out = malloc(osz), *mid = malloc((size_t)oh * iw);
	(void)d; double total = 0; int frames = 0;
	while (fgets(line, sizeof line, stdin)) {
		if (fread(in, 1, isz, stdin) != isz) break;
		struct timespec a, b; clock_gettime(CLOCK_MONOTONIC, &a);
		plane(in, iw, ih, out, ow, oh, &X[0], &Y[0], mid);
		plane(in + (size_t)iw * ih, iw / 2, ih / 2, out + (size_t)ow * oh, ow / 2, oh / 2, &X[1], &Y[1], mid);
		plane(in + (size_t)iw * ih * 5 / 4, iw / 2, ih / 2, out + (size_t)ow * oh * 5 / 4, ow / 2, oh / 2, &X[1], &Y[1], mid);
		clock_gettime(CLOCK_MONOTONIC, &b); total += (b.tv_sec - a.tv_sec) * 1e3 + (b.tv_nsec - a.tv_nsec) / 1e6; frames++;
		fputs("FRAME\n", stdout); fwrite(out, 1, osz, stdout);
	}
	if (timing) fprintf(stderr, "%d frames, %.2f ms/frame (filter only)\n", frames, total / (frames ? frames : 1));
	return 0;
}
