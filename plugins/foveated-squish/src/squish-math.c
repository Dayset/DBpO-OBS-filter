/*
Foveated Squish
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

#include "squish-math.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static double clamp01(double v)
{
	return v < 0.0 ? 0.0 : (v > 1.0 ? 1.0 : v);
}

void squish_axis_derive(const struct squish_axis *a, struct squish_axis_params *o)
{
	o->lo = clamp01(a->f - a->c);
	o->hi = clamp01(a->f + a->c);
	o->ll = o->lo;
	o->lr = 1.0 - o->hi;
	const double total = o->ll + o->lr;
	o->dmin = total <= 1e-9 ? 1.0 : 1.0 - (1.0 - a->r) * (a->p + 1.0) / total;
	o->k = (1.0 - o->dmin) / (a->p + 1.0);
}

double squish_axis_dmin(const struct squish_axis *a)
{
	struct squish_axis_params o;
	squish_axis_derive(a, &o);
	return o.dmin;
}

double squish_axis_min_r_floor(const struct squish_axis *a, double floor)
{
	struct squish_axis_params o;
	squish_axis_derive(a, &o);
	return 1.0 - (1.0 - floor) * (o.ll + o.lr) / (a->p + 1.0);
}

double squish_axis_min_r(const struct squish_axis *a)
{
	return squish_axis_min_r_floor(a, SQUISH_MIN_DENSITY);
}

static double periph(double t, double length, double k, double p)
{
	return length * (t - k * pow(t, p + 1.0));
}

double squish_forward(const struct squish_axis *a, double x)
{
	struct squish_axis_params o;
	squish_axis_derive(a, &o);
	const double left_full = periph(1.0, o.ll, o.k, a->p);
	double u;
	if (x < o.lo)
		u = left_full - periph(clamp01((o.lo - x) / fmax(o.ll, 1e-12)), o.ll, o.k, a->p);
	else if (x <= o.hi)
		u = left_full + (x - o.lo);
	else
		u = left_full + (o.hi - o.lo) + periph(clamp01((x - o.hi) / fmax(o.lr, 1e-12)), o.lr, o.k, a->p);
	return u / a->r;
}

double squish_density(const struct squish_axis *a, double x)
{
	struct squish_axis_params o;
	squish_axis_derive(a, &o);
	double t = 0.0;
	if (x < o.lo)
		t = (o.lo - x) / fmax(o.ll, 1e-12);
	else if (x > o.hi)
		t = (x - o.hi) / fmax(o.lr, 1e-12);
	return 1.0 - (1.0 - o.dmin) * pow(clamp01(t), a->p);
}

/* Solve len * (t - k t^(p+1)) = target for t in [0, 1]: Newton kept inside a
 * shrinking bracket (bisection when a step leaves it). The function is
 * increasing (its slope is the density), so this always converges; plain
 * Newton cycles for flat curves with strong squeeze. */
static double solve_periph(double target, double len, double k, double p)
{
	if (len <= 1e-12)
		return 0.0;
	double lo = 0.0, hi = 1.0, t = clamp01(target / len);
	for (int i = 0; i < SQUISH_INVERSE_ITERS; i++) {
		const double f = len * (t - k * pow(t, p + 1.0)) - target;
		if (f > 0.0)
			hi = t;
		else
			lo = t;
		const double d = len * (1.0 - k * (p + 1.0) * pow(t, p));
		double tn = d > 1e-12 ? t - f / d : 0.5 * (lo + hi);
		if (!(tn >= lo && tn <= hi))
			tn = 0.5 * (lo + hi);
		t = tn;
	}
	return t;
}

double squish_inverse(const struct squish_axis *a, double u)
{
	struct squish_axis_params o;
	squish_axis_derive(a, &o);
	const double v = clamp01(u) * a->r; /* integral of density up to x */
	const double left_full = periph(1.0, o.ll, o.k, a->p);
	if (v < left_full)
		return clamp01(o.lo - o.ll * solve_periph(left_full - v, o.ll, o.k, a->p));
	if (v <= left_full + (o.hi - o.lo))
		return o.lo + (v - left_full);
	return clamp01(o.hi + o.lr * solve_periph(v - left_full - (o.hi - o.lo), o.lr, o.k, a->p));
}

double squish_axis_align(const struct squish_axis *a, unsigned n, unsigned m)
{
	struct squish_axis_params o;
	squish_axis_derive(a, &o);
	/* Center: x*n = (j + 0.5) + align*m + n*(lo - left_full). */
	const double d = (double)n * (o.lo - periph(1.0, o.ll, o.k, a->p));
	return (floor(d + 0.5) - d) / (double)m;
}

void squish_config_format(const struct squish_config *cfg, char *buf, size_t size)
{
	snprintf(buf, size, "FSQ1:rx=%.4g,ry=%.4g,cx=%.4g,cy=%.4g,fx=%.4g,fy=%.4g,px=%.4g,py=%.4g,ar=%.5g", cfg->x.r,
		 cfg->y.r, cfg->x.c, cfg->y.c, cfg->x.f, cfg->y.f, cfg->x.p, cfg->y.p, cfg->ar);
	if (cfg->boxed) {
		const size_t n = strlen(buf);
		if (n < size)
			snprintf(buf + n, size - n, ",ox=%.6g,oy=%.6g,bw=%.6g,bh=%.6g", cfg->ox, cfg->oy, cfg->bw,
				 cfg->bh);
	}
}

bool squish_config_parse(const char *s, struct squish_config *cfg)
{
	while (*s == ' ' || *s == '\t' || *s == '\n' || *s == '\r')
		s++;
	if (strncmp(s, "FSQ1:", 5) != 0)
		return false;
	struct squish_config c = {
		{0.75, 0.15, 0.5, 1.0}, {0.75, 0.15, 0.5, 1.0}, 16.0 / 9.0, false, 0.0, 0.0, 1.0, 1.0};
	const char *p = s + 5;
	while (*p) {
		char key[8] = {0};
		int n = 0;
		while (*p && *p != '=' && n < 7)
			key[n++] = *p++;
		if (*p != '=')
			return false;
		p++;
		char *end = NULL;
		double v = strtod(p, &end);
		if (end == p || !isfinite(v))
			return false;
		p = end;
		if (!strcmp(key, "rx"))
			c.x.r = v;
		else if (!strcmp(key, "ry"))
			c.y.r = v;
		else if (!strcmp(key, "cx"))
			c.x.c = v;
		else if (!strcmp(key, "cy"))
			c.y.c = v;
		else if (!strcmp(key, "fx"))
			c.x.f = v;
		else if (!strcmp(key, "fy"))
			c.y.f = v;
		else if (!strcmp(key, "px"))
			c.x.p = v;
		else if (!strcmp(key, "py"))
			c.y.p = v;
		else if (!strcmp(key, "ar"))
			c.ar = v;
		else if (!strcmp(key, "ox"))
			c.ox = v, c.boxed = true;
		else if (!strcmp(key, "oy"))
			c.oy = v, c.boxed = true;
		else if (!strcmp(key, "bw"))
			c.bw = v, c.boxed = true;
		else if (!strcmp(key, "bh"))
			c.bh = v, c.boxed = true;
		if (*p == ',')
			p++;
		else if (*p && *p != ' ' && *p != '\n' && *p != '\r')
			return false;
		else
			break;
	}
	if (c.x.r <= 0 || c.x.r > 1 || c.y.r <= 0 || c.y.r > 1 || c.x.p <= 0 || c.y.p <= 0 || c.ar <= 0)
		return false;
	if (c.boxed &&
	    (c.ox < 0 || c.oy < 0 || c.bw <= 0 || c.bh <= 0 || c.ox + c.bw > 1.0 + 1e-6 || c.oy + c.bh > 1.0 + 1e-6))
		return false;
	*cfg = c;
	return true;
}
