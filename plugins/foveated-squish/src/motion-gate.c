#include "motion-gate.h"

static double clamp01(double v)
{
	return v < 0 ? 0 : v > 1 ? 1 : v;
}

struct motion_gate_config motion_gate_default_config(void)
{
	struct motion_gate_config c = {
		.flow_full_px = 6.0,
		.up = {0.15, 0.35, 0.60},
		.down = {0.10, 0.25, 0.50},
		.hold_ms = 500.0,
	};
	return c;
}

void motion_gate_init(struct motion_gate *g, const struct motion_gate_config *cfg)
{
	g->cfg = *cfg;
	g->level = 0;
	g->held_ms = 0;
}

double motion_value(const struct motion_gate_config *cfg, const struct motion_sample *s)
{
	/* Too little matching means the flow can't be trusted, so treat it as no motion. */
	if (s->match_quality < 0.5)
		return 0;
	return clamp01(s->mean_flow_px / cfg->flow_full_px);
}

bool motion_gate_update(struct motion_gate *g, const struct motion_sample *s, double frame_ms)
{
	g->held_ms += frame_ms;
	if (g->held_ms < g->cfg.hold_ms)
		return false;
	double m = motion_value(&g->cfg, s);
	int want = g->level;
	/* Rise: step up while m clears the next up threshold. */
	while (want < MOTION_LEVELS - 1 && m >= g->cfg.up[want])
		want++;
	/* Fall: step down while m is under the down threshold of the current level. */
	while (want > 0 && m < g->cfg.down[want - 1])
		want--;
	if (want == g->level)
		return false;
	g->level = want;
	g->held_ms = 0;
	return true;
}
