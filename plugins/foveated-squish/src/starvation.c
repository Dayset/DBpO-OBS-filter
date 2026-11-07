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
	struct stv_controller_cfg c = {budget_kbps, 1.35, 1.0 / 8.0, 6, 60, 3, {1.5, 3.0, 6.0, 12.0, 24.0, 1e18}};
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
	int want = 5;
	for (int i = 0; i < 6; i++) {
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

/* ---------------- marker v2, ramps, demo ---------------- */

double stv_frac(int a)
{
	if (a < 0)
		a = 0;
	if (a > STV_A_MAX)
		a = STV_A_MAX;
	return 1.0 - (1.0 - STV_F_MIN) * a / STV_A_MAX;
}

double stv_squeeze(int a, int q)
{
	if (q < 0)
		q = 0;
	if (q > STV_Q_MAX)
		q = STV_Q_MAX;
	return pow(stv_frac(a), (double)q / STV_Q_MAX);
}

int stv_a_for(double f)
{
	const double v = floor((1.0 - f) / (1.0 - STV_F_MIN) * STV_A_MAX + 0.5);
	return v < 0 ? 0 : v > STV_A_MAX ? STV_A_MAX : (int)v;
}

struct stv_rect stv_rect2(int a, int pw, int ph)
{
	const double f = stv_frac(a);
	struct stv_rect r = {round_even(f * pw), round_even(f * ph)};
	return r;
}

void stv_ext_pos(int frame_w, int frame_h, int *x, int *y)
{
	stv_marker_pos(frame_w, frame_h, x, y);
	*y -= 16;
}

void stv_ext_bits(int a, int q, int bits[STV_EXT_BITS])
{
	int ones = 0;
	for (int i = 0; i < 6; i++)
		ones += bits[i] = (a >> i) & 1;
	for (int i = 0; i < 3; i++)
		ones += bits[6 + i] = (q >> i) & 1;
	bits[9] = ones & 1;
}

unsigned stv_cells_v1(int code)
{
	uint8_t cells[2 * STV_BITS];
	stv_marker_cells(code, cells);
	unsigned m = 0;
	for (int i = 0; i < 2 * STV_BITS; i++)
		m |= (unsigned)cells[i] << i;
	return m;
}

unsigned stv_cells_ext(int a, int q)
{
	int bits[STV_EXT_BITS];
	stv_ext_bits(a, q, bits);
	unsigned m = 0;
	for (int i = 0; i < STV_EXT_BITS; i++)
		m |= (unsigned)(bits[i] ? 1u : 2u) << (2 * i); /* Manchester: 1 = white then black */
	return m;
}

void stv_ramp_init(struct stv_ramp *r)
{
	*r = (struct stv_ramp){0};
}

void stv_ramp_go(struct stv_ramp *r, int a, int q, int frames)
{
	if (r->ta == a && r->tq == q && r->i < r->n)
		return;
	r->a0 = r->a;
	r->q0 = r->q;
	r->ta = a;
	r->tq = q;
	r->n = frames < 1 ? 1 : frames;
	r->i = 0;
}

void stv_ramp_step(struct stv_ramp *r, int *a, int *q)
{
	if (r->i < r->n) {
		r->i++;
		const double t = (double)r->i / r->n;
		r->a = r->a0 + (r->ta - r->a0) * t;
		r->q = r->q0 + (r->tq - r->q0) * t;
	}
	*a = (int)floor(r->a + 0.5);
	*q = (int)floor(r->q + 0.5);
}

const double stv_demo[STV_DEMO_PHASES][4] = {{0, 0, 2.0, 4.0},
					     {38, 7, 2.0, 4.0},
					     {38, 0, 2.0, 4.0},
					     {50, 0, 2.0, 4.0},
					     {50, 7, 2.0, 4.0}};

void stv_demo_step(struct stv_demo_state *d, struct stv_ramp *r, double fps)
{
	const double *p = stv_demo[d->phase];
	if (d->frame == 0)
		stv_ramp_go(r, (int)p[0], (int)p[1], (int)floor(p[2] * fps + 0.5));
	if (++d->frame >= (int)floor((p[2] + p[3]) * fps + 0.5)) {
		d->frame = 0;
		d->phase = (d->phase + 1) % STV_DEMO_PHASES;
	}
}

double stv_target_frac(int idx)
{
	static const double f[6] = {1.0, 2.0 / 3.0, 0.5, 1.0 / 3.0, 0.25, 1.0 / 6.0};
	return f[idx < 0 ? 0 : idx > 5 ? 5 : idx];
}
