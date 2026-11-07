#include "starvation.h"

#include <math.h>

static const double fraction[STV_LEVELS] = {1.0, 2.0 / 3.0, 0.5, 1.0 / 3.0, 0.75, 0.5};

enum stv_kind stv_kind(int code)
{
	return code >= 4 && code < STV_LEVELS ? STV_SQUISH : STV_PLAIN;
}

static int round_even(double v)
{
	return (int)floor(v + 0.5) & ~1;
}

struct stv_rect stv_rect(int code, int pw, int ph)
{
	if (code < 0 || code >= STV_LEVELS)
		code = 0;
	struct stv_rect r = {round_even(fraction[code] * pw), round_even(fraction[code] * ph)};
	return r;
}

void stv_marker_bits(int code, int bits[STV_BITS])
{
	int ones = 0;
	for (int i = 0; i < 4; i++) {
		bits[i] = (code >> i) & 1;
		ones += bits[i];
	}
	bits[4] = ones & 1;
}

void stv_marker_cells(int code, uint8_t cells[2 * STV_BITS])
{
	int bits[STV_BITS];
	stv_marker_bits(code, bits);
	for (int i = 0; i < STV_BITS; i++) {
		cells[2 * i] = (uint8_t)bits[i];
		cells[2 * i + 1] = (uint8_t)(1 - bits[i]);
	}
}

void stv_marker_pos(int frame_w, int frame_h, int *x, int *y)
{
	*x = frame_w - STV_MARKER_W;
	*y = (frame_h - 24) / 16 * 16;
}

int stv_marker_decode(const double m[2 * STV_BITS])
{
	int bits[STV_BITS];
	for (int i = 0; i < STV_BITS; i++) {
		if (fabs(m[2 * i] - m[2 * i + 1]) < 4.0)
			return -1;
		bits[i] = m[2 * i] > m[2 * i + 1];
	}
	if (((bits[0] + bits[1] + bits[2] + bits[3]) & 1) != bits[4])
		return -1;
	return bits[0] | bits[1] << 1 | bits[2] << 2 | bits[3] << 3;
}

struct stv_controller_cfg stv_default_cfg(double budget_kbps)
{
	struct stv_controller_cfg c = {budget_kbps, 1.35, 1.0 / 8.0, 6, 60, 3, {1.5, 3.0, 6.0, 1e18}};
	return c;
}

void stv_controller_init(struct stv_controller *c, const struct stv_controller_cfg *cfg)
{
	*c = (struct stv_controller){.cfg = *cfg};
}

static double median3(const double *h, int n)
{
	if (n == 1)
		return h[0];
	if (n == 2)
		return (h[0] + h[1]) / 2.0; /* numpy median of two values */
	double a = h[0], b = h[1], c = h[2];
	if (a > b) {
		double t = a;
		a = b;
		b = t;
	}
	if (b > c) {
		double t = b;
		b = c;
		c = t;
	}
	if (a > b) {
		double t = a;
		a = b;
		b = t;
	}
	return b;
}

int stv_controller_step(struct stv_controller *c, double metric, double congestion)
{
	if (c->hist_n < 3) {
		c->hist[c->hist_n++] = metric;
	} else {
		c->hist[0] = c->hist[1];
		c->hist[1] = c->hist[2];
		c->hist[2] = metric;
	}
	double med = median3(c->hist, c->hist_n);
	if (!c->have_ema) {
		c->ema = med;
		c->have_ema = true;
	} else {
		c->ema += (med - c->ema) * c->cfg.alpha;
	}
	if (congestion < 0)
		congestion = 0;
	if (congestion > 0.9)
		congestion = 0.9;
	double budget = c->cfg.budget_kbps * (1.0 - congestion);
	if (budget < 1.0)
		budget = 1.0;
	c->ratio = c->ema * c->cfg.k * 1000.0 / budget;
	int want = 3;
	for (int i = 0; i < 4; i++) {
		if (c->ratio < c->cfg.table[i]) {
			want = i;
			break;
		}
	}
	if (want > c->cfg.lowest)
		want = c->cfg.lowest;
	c->run = want == c->want ? c->run + 1 : 1;
	c->want = want;
	if ((want > c->cur && c->run >= c->cfg.down_frames) || (want < c->cur && c->run >= c->cfg.up_frames))
		c->cur = want;
	return c->cur;
}
