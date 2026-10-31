#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include "camera-model.h"
#define W 64
#define H 36
static float g[H][W][4];
static float rnd(void)
{
	return (float)rand() / RAND_MAX * 2 - 1;
}
static int check(const char *name, const struct camera_model *m, const float ex[3], const float ey[3])
{
	float err = 0;
	for (int i = 0; i < 3; i++) {
		err = fmaxf(err, fabsf(m->x[i] - ex[i]));
		err = fmaxf(err, fabsf(m->y[i] - ey[i]));
	}
	printf("%-34s x=(%6.2f %6.2f %6.2f) y=(%6.2f %6.2f %6.2f) maxerr=%.2f %s\n", name, m->x[0], m->x[1], m->x[2],
	       m->y[0], m->y[1], m->y[2], err, err < 1.0 ? "PASS" : "FAIL");
	return err < 1.0;
}
int main(void)
{
	srand(1);
	int ok = 1;
	struct camera_model m;
	/* Forward motion: zoom 40 px across half-frame, drift x=+3,y=-2 */
	const float ex[3] = {3, 40, 0}, ey[3] = {-2, 0, 40};
	for (int y = 0; y < H; y++)
		for (int x = 0; x < W; x++) {
			float px = (x + .5f) / W - .5f, py = (y + .5f) / H - .5f;
			float *p = g[y][x];
			p[0] = ex[0] + ex[1] * px + ex[2] * py + rnd() * 1.5f;
			p[1] = ey[0] + ey[1] * px + ey[2] * py + rnd() * 1.5f;
			p[2] = 0.7f;
			p[3] = 1;
			if (x > 40 && x < 56 && y > 10 && y < 22) {
				p[0] = -25;
				p[1] = 4;
			} /* moving object ~15% */
			if (y >= H - 4 && x < 20) {
				p[0] = 0;
				p[1] = 0;
				p[2] = 0.9f;
			} /* static HUD, confident */
			if (y < 6) {
				p[0] = 0;
				p[1] = 0;
				p[2] = 0.02f;
			} /* flat sky, low confidence */
		}
	ok &= camera_model_fit(&g[0][0][0], W, H, W * 4, 0.1f, &m);
	ok &= check("zoom+drift, object, HUD, sky", &m, ex, ey);
	/* Pure pan */
	const float px3[3] = {-12, 0, 0}, py3[3] = {0, 0, 0};
	for (int y = 0; y < H; y++)
		for (int x = 0; x < W; x++) {
			float *p = g[y][x];
			p[0] = -12 + rnd();
			p[1] = rnd();
			p[2] = .6f;
		}
	ok &= camera_model_fit(&g[0][0][0], W, H, W * 4, 0.1f, &m);
	ok &= check("pure pan", &m, px3, py3);
	/* Static scene with one moving object -> model ~0 */
	const float z[3] = {0, 0, 0};
	for (int y = 0; y < H; y++)
		for (int x = 0; x < W; x++) {
			float *p = g[y][x];
			p[0] = p[1] = 0;
			p[2] = .6f;
			if (x < 20 && y < 15) {
				p[0] = 30;
				p[1] = 0;
			}
		}
	ok &= camera_model_fit(&g[0][0][0], W, H, W * 4, 0.1f, &m);
	ok &= check("static + object (expect zero)", &m, z, z);
	/* Nothing reliable -> false */
	for (int y = 0; y < H; y++)
		for (int x = 0; x < W; x++)
			g[y][x][2] = 0.01f;
	int r = camera_model_fit(&g[0][0][0], W, H, W * 4, 0.1f, &m);
	printf("%-34s returns %s %s\n", "no confident samples", r ? "true" : "false", !r ? "PASS" : "FAIL");
	ok &= !r;
	printf(ok ? "ALL PASS\n" : "SOME FAILED\n");
	return !ok;
}
