#include <stdio.h>
#include <string.h>

#include "starvation.h"
#include "starvation_golden.h"

static int fails;
#define CHECK(c, msg)                                       \
	do {                                                \
		if (!(c)) {                                 \
			fprintf(stderr, "FAIL: %s\n", msg); \
			fails++;                            \
		}                                           \
	} while (0)

int main(void)
{
	for (int f = 0; f < 3; f++) {
		for (int c = 0; c < 6; c++) {
			struct stv_rect r = stv_rect(c, stv_golden_frames[f][0], stv_golden_frames[f][1]);
			CHECK(r.w == stv_golden_rects[f][c][0] && r.h == stv_golden_rects[f][c][1],
			      "rect matches Python");
		}
		int x, y;
		stv_marker_pos(stv_golden_frames[f][0], stv_golden_frames[f][1], &x, &y);
		CHECK(x == stv_golden_marker_pos[f][0] && y == stv_golden_marker_pos[f][1], "marker position");
	}
	for (int c = 0; c < 6; c++) {
		int bits[STV_BITS];
		stv_marker_bits(c, bits);
		CHECK(memcmp(bits, stv_golden_bits[c], sizeof bits) == 0, "marker bits");
		/* encode -> decode through ideal luma */
		uint8_t cells[10];
		double m[10];
		stv_marker_cells(c, cells);
		for (int i = 0; i < 10; i++)
			m[i] = cells[i] ? 235.0 : 16.0;
		CHECK(stv_marker_decode(m) == c, "marker round trip");
		m[0] = 235.0 - (m[0] - 16.0); /* flip one cell: parity must fail */
		CHECK(stv_marker_decode(m) == -1, "parity reject");
	}
	{
		double flat[10] = {90, 90, 90, 90, 90, 90, 90, 90, 90, 90};
		CHECK(stv_marker_decode(flat) == -1, "blank frame has no marker");
	}

	for (int pass = 0; pass < 2; pass++) {
		double kb = pass ? 3000 : 6000;
		const unsigned char *want = pass ? stv_golden_sched_3000 : stv_golden_sched_6000;
		struct stv_controller_cfg cfg = stv_default_cfg(kb);
		struct stv_controller ctl;
		stv_controller_init(&ctl, &cfg);
		int bad = 0;
		for (int i = 0; i < STV_GOLDEN_N; i++) {
			if (ctl.cur != want[i])
				bad++;
			stv_controller_step(&ctl, stv_golden_trace[i], 0.0);
		}
		CHECK(bad == 0,
		      pass ? "controller 3000 matches Python schedule" : "controller 6000 matches Python schedule");
	}

	/* Congestion lowers the budget: a mild load that is fine at full budget drops a level. */
	{
		struct stv_controller_cfg cfg = stv_default_cfg(6000);
		struct stv_controller a, b;
		stv_controller_init(&a, &cfg);
		stv_controller_init(&b, &cfg);
		for (int i = 0; i < 200; i++) {
			stv_controller_step(&a, 5.0, 0.0);
			stv_controller_step(&b, 5.0, 0.6);
		}
		CHECK(a.cur == 0 && b.cur > 0, "congestion drops the level");
	}
	/* Lowest allowed level is respected. */
	{
		struct stv_controller_cfg cfg = stv_default_cfg(1000);
		cfg.lowest = 2;
		struct stv_controller c;
		stv_controller_init(&c, &cfg);
		for (int i = 0; i < 200; i++)
			stv_controller_step(&c, 30.0, 0.0);
		CHECK(c.cur == 2, "lowest allowed level caps the drop");
	}

	fprintf(stderr, "starvation: %s\n", fails ? "FAIL" : "PASS");
	return fails ? 1 : 0;
}
