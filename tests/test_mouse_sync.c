#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include "mouse-sync.h"
static float rnd(void)
{
	return (float)rand() / RAND_MAX * 2 - 1;
}
#define N 3000
static float mx[N], my[N];
static int run(const char *name, float gx, float gy, int lag, float noise, float strafe_frac, int expect_ready)
{
	struct mouse_sync s;
	mouse_sync_reset(&s);
	/* bursts of mouse motion, like aiming */
	float vx = 0, vy = 0;
	for (int k = 0; k < N; k++) {
		if (rand() % 20 == 0) {
			vx = rnd() * 30;
			vy = rnd() * 12;
		}
		if (rand() % 6 == 0) {
			vx *= 0.3f;
			vy *= 0.3f;
		}
		mx[k] = roundf(vx);
		my[k] = roundf(vy);
	}
	for (int k = MOUSE_MAX_LAG; k < N; k++) {
		float px = (k - lag >= 0 ? gx * mx[k - lag] : 0) + rnd() * noise;
		float py = (k - lag >= 0 ? gy * my[k - lag] : 0) + rnd() * noise;
		if ((float)rand() / RAND_MAX < strafe_frac)
			px += rnd() * 8; /* camera motion not from mouse */
		float m[MOUSE_MAX_LAG + 1][2];
		bool v[MOUSE_MAX_LAG + 1];
		for (int l = 0; l <= MOUSE_MAX_LAG; l++) {
			m[l][0] = mx[k - l];
			m[l][1] = my[k - l];
			v[l] = true;
		}
		mouse_sync_observe(&s, m, v, px, py);
	}
	int ok = s.ready == expect_ready;
	if (expect_ready)
		ok = ok && s.lag == lag && fabsf(s.gain[0] - gx) < 0.03f * fabsf(gx) + 0.01f &&
		     fabsf(s.gain[1] - gy) < 0.03f * fabsf(gy) + 0.01f;
	printf("%-36s gain=(%6.3f %6.3f) lag=%d corr=%.2f ready=%d  %s\n", name, s.gain[0], s.gain[1], s.lag,
	       s.correlation, s.ready, ok ? "PASS" : "FAIL");
	return ok;
}
int main(void)
{
	srand(7);
	int ok = 1;
	ok &= run("x right->scene left, lag 2", -0.85f, -0.85f, 2, 0.6f, 0.0f, 1);
	ok &= run("inverted Y, lag 0, high sens", -2.4f, 2.4f, 0, 0.8f, 0.0f, 1);
	ok &= run("lag 4 + 20% strafing", -0.5f, -0.5f, 4, 0.5f, 0.2f, 1);
	ok &= run("menu: mouse moves, camera doesn't", 0.0f, 0.0f, 1, 0.7f, 0.0f, 0);
	printf(ok ? "ALL PASS\n" : "SOME FAILED\n");
	return !ok;
}
