#pragma once

#include <stdbool.h>

/* Learns how raw mouse counts map to on-screen camera pan:
 *   pan_px(frame k) = gain * mouse_counts(frame k - lag)
 * per axis (gain carries in-game sensitivity, FOV and axis inversion), and
 * the input-to-capture latency `lag` in frames. Fitted continuously with
 * exponential forgetting from frames where the image-based camera fit is
 * trustworthy, so it adapts if sensitivity or the game changes. */

#define MOUSE_MAX_LAG 4

struct mouse_sync {
	double mm[2][MOUSE_MAX_LAG + 1]; /* sum mouse^2 */
	double mf[2][MOUSE_MAX_LAG + 1]; /* sum mouse * measured pan */
	double ff[2];                    /* sum measured pan^2 */
	double active;                   /* effective count of frames with mouse movement */
	float gain[2];
	int lag;
	float correlation; /* 0..1, how well mouse explains measured pan */
	bool ready;
};

void mouse_sync_reset(struct mouse_sync *s);

/* One observation: measured camera pan (px) for some frame k, and the mouse
 * deltas for frames k, k-1, ... k-MOUSE_MAX_LAG (mouse[l] = frame k - l).
 * valid[l] is false where that history isn't available. */
void mouse_sync_observe(struct mouse_sync *s, const float mouse[MOUSE_MAX_LAG + 1][2],
			const bool valid[MOUSE_MAX_LAG + 1], float pan_x, float pan_y);
