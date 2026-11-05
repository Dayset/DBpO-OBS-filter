#include <stdio.h>

#include "motion-gate.h"

static int fails;
#define CHECK(c, msg)                                            \
	do {                                                     \
		if (!(c)) {                                      \
			fprintf(stderr, "FAIL: %s\n", msg);      \
			fails++;                                 \
		}                                                \
	} while (0)


int main(void)
{
	struct motion_gate_config cfg = motion_gate_default_config();
	struct motion_gate g;
	motion_gate_init(&g, &cfg);

	/* Static menu: no flow, perfect match -> stays at level 0. */
	for (int i = 0; i < 100; i++) motion_gate_update(&g, &(struct motion_sample){0, 1}, 16.7);
	CHECK(g.level == 0, "static screen stays at level 0");

	/* Fast spin: strong flow -> reaches the top level once the hold time passes. */
	for (int i = 0; i < 100; i++) motion_gate_update(&g, &(struct motion_sample){6, 0.9}, 16.7);
	CHECK(g.level == MOTION_LEVELS - 1, "fast spin reaches strongest level");

	/* Untrusted matching (low quality) counts as no motion, so the level falls. */
	for (int i = 0; i < 100; i++) motion_gate_update(&g, &(struct motion_sample){6, 0.2}, 16.7);
	CHECK(g.level == 0, "low match quality falls back to level 0");

	/* Hysteresis: a value between down and up thresholds does not flicker. */
	motion_gate_init(&g, &cfg);
	for (int i = 0; i < 100; i++) motion_gate_update(&g, &(struct motion_sample){6 * 0.2, 1}, 16.7);
	int first = g.level;
	int changes = 0;
	for (int i = 0; i < 300; i++) {
		double f = (i % 2) ? 6 * 0.12 : 6 * 0.18; /* m alternates between 0.12 and 0.18 */
		if (motion_gate_update(&g, &(struct motion_sample){f, 1}, 16.7)) changes++;
	}
	CHECK(changes == 0 && g.level == first, "hysteresis band causes no changes");

	/* Hold time: a fast change inside the hold window is not applied. */
	motion_gate_init(&g, &cfg);
	bool changed = motion_gate_update(&g, &(struct motion_sample){6, 1}, 100.0);
	CHECK(!changed && g.level == 0, "level does not change within hold time");

	fprintf(stderr, "motion gate: %s\n", fails ? "FAIL" : "PASS");
	return fails ? 1 : 0;
}
