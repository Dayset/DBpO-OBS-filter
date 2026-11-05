#include <stdio.h>
#include <string.h>

#include "atlas-math.h"
#include "atlas_golden.h"

int main(void)
{
	int fails = 0;
	const int n = (int)(sizeof(atlas_golden) / sizeof(atlas_golden[0]));
	for (int c = 0; c < n; c++) {
		struct atlas a;
		const struct atlas_golden_case *g = &atlas_golden[c];
		if (!atlas_parse(g->config, &a)) {
			fprintf(stderr, "case %d: parse failed\n", c);
			fails++;
			continue;
		}
		if (a.count != g->regions) {
			fprintf(stderr, "case %d: region count %d != %d\n", c, a.count, g->regions);
			fails++;
			continue;
		}
		if (g->regions == 0)
			continue;
		struct atlas_slot slots[ATLAS_MAX_REGIONS];
		if (!atlas_layout(&a, 1920, 1080, 2560, 1440, slots)) {
			fprintf(stderr, "case %d: layout failed\n", c);
			fails++;
			continue;
		}
		for (int i = 0; i < g->regions; i++) {
			int want[4] = {g->slots[i][0], g->slots[i][1], g->slots[i][2], g->slots[i][3]};
			int got[4] = {slots[i].x, slots[i].y, slots[i].w, slots[i].h};
			if (memcmp(want, got, sizeof(want))) {
				fprintf(stderr, "case %d slot %d: got %d,%d,%d,%d want %d,%d,%d,%d\n", c, i,
					got[0], got[1], got[2], got[3], want[0], want[1], want[2], want[3]);
				fails++;
			}
		}
	}
	fprintf(stderr, "atlas golden: %d cases, %s\n", n, fails ? "FAIL" : "PASS");
	return fails ? 1 : 0;
}
