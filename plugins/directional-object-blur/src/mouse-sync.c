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

#include "mouse-sync.h"

#include <math.h>
#include <string.h>

/* ~10 s memory at 60 fps. */
#define FORGET 0.9985
#define MIN_ACTIVE_FRAMES 30.0
#define MIN_CORRELATION 0.45

void mouse_sync_reset(struct mouse_sync *s)
{
	memset(s, 0, sizeof(*s));
}

void mouse_sync_observe(struct mouse_sync *s, const float mouse[MOUSE_MAX_LAG + 1][2],
			const bool valid[MOUSE_MAX_LAG + 1], float pan_x, float pan_y)
{
	const float pan[2] = {pan_x, pan_y};
	bool moved = false;

	for (int a = 0; a < 2; a++) {
		s->ff[a] = s->ff[a] * FORGET + (double)pan[a] * pan[a];
		for (int l = 0; l <= MOUSE_MAX_LAG; l++) {
			s->mm[a][l] *= FORGET;
			s->mf[a][l] *= FORGET;
			if (!valid[l])
				continue;
			s->mm[a][l] += (double)mouse[l][a] * mouse[l][a];
			s->mf[a][l] += (double)mouse[l][a] * pan[a];
			moved |= mouse[l][a] != 0.0f;
		}
	}
	s->active = s->active * FORGET + (moved ? 1.0 : 0.0);

	/* Pick the lag whose regression explains the most pan energy across
	 * both axes. */
	double ff_total = s->ff[0] + s->ff[1];
	double best_score = -1.0;
	int best_lag = 0;
	for (int l = 0; l <= MOUSE_MAX_LAG; l++) {
		double explained = 0.0;
		for (int a = 0; a < 2; a++) {
			if (s->mm[a][l] > 1e-9)
				explained += s->mf[a][l] * s->mf[a][l] / s->mm[a][l];
		}
		double score = ff_total > 1e-9 ? explained / ff_total : 0.0;
		if (score > best_score) {
			best_score = score;
			best_lag = l;
		}
	}

	s->lag = best_lag;
	for (int a = 0; a < 2; a++) {
		double mm = s->mm[a][best_lag];
		s->gain[a] = mm > 1e-9 ? (float)(s->mf[a][best_lag] / mm) : 0.0f;
	}
	s->correlation = best_score > 0.0 ? (float)sqrt(best_score) : 0.0f;
	s->ready = s->active >= MIN_ACTIVE_FRAMES && s->correlation >= MIN_CORRELATION;
}
