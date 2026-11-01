#pragma once

/* Foveated Squish mapping, per axis, normalized coordinates (see
 * research/foveation/squish_math.py, the reference these functions are
 * tested against).
 *
 *   density d(x) = 1 in the sharp center, 1 - (1 - dmin) * t^p in the
 *   periphery (t: 0 at the center edge .. 1 at the frame edge).
 *   U(x) = integral of d / r, r = squished size / source size.
 */

#include <stdbool.h>
#include <stddef.h>

#define SQUISH_MIN_DENSITY 0.08

struct squish_axis {
	double r; /* squished / source size, (0, 1] */
	double c; /* half-size of the sharp center, fraction of the axis */
	double f; /* focus position, 0..1 */
	double p; /* curve: 1 linear, <1 squeeze ramps up fast, >1 edges squeezed hardest */
};

/* Derived values, shared with the shader. */
struct squish_axis_params {
	double lo, hi; /* sharp center bounds */
	double ll, lr; /* periphery lengths left / right */
	double k;      /* (1 - dmin) / (p + 1) */
	double dmin;
};

void squish_axis_derive(const struct squish_axis *a, struct squish_axis_params *out);
double squish_axis_dmin(const struct squish_axis *a);
double squish_axis_min_r(const struct squish_axis *a); /* strongest squeeze allowed */
double squish_forward(const struct squish_axis *a, double x);
double squish_density(const struct squish_axis *a, double x);
double squish_inverse(const struct squish_axis *a, double u);

/* Shift (normalized squished units) that puts squished pixel centers inside
 * the sharp center exactly on source pixel centers, for n source and m
 * squished pixels. Always under half a squished pixel. */
double squish_axis_align(const struct squish_axis *a, unsigned n, unsigned m);

struct squish_config {
	struct squish_axis x, y;
	double ar; /* source aspect ratio */
};

/* "FSQ1:rx=..,ry=..,cx=..,cy=..,fx=..,fy=..,px=..,py=..,ar=.." */
void squish_config_format(const struct squish_config *cfg, char *buf, size_t size);
bool squish_config_parse(const char *s, struct squish_config *cfg);
