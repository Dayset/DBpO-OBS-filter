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
#define SQUISH_INVERSE_ITERS 20 /* same count in the shader */

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
double squish_axis_min_r(const struct squish_axis *a); /* strongest squeeze at SQUISH_MIN_DENSITY */
/* Strongest squeeze (smallest r) whose edge density stays >= floor. */
double squish_axis_min_r_floor(const struct squish_axis *a, double floor);
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
	/* Boxed layout: the squished picture occupies the rectangle (ox, oy,
	 * bw, bh) of the streamed frame (fractions); the rest is black bars. */
	bool boxed;
	double ox, oy, bw, bh;
	/* Viewer look, applied only in the viewer's browser (never streamed). Details
	 * (sharpen, clarity, HDR toning, bloom) and Sharpen+ are 0..1; the colour
	 * values are signed -1..1 (brightness .. shadows) or 0..1 (bw, sepia, night).
	 * 0 = off. Sent as a look code ("※" + CJK characters, see squish_look_pack)
	 * after the numbers, only when something is set; the old keys ls, lc, lh, lb,
	 * lp are still read. Math: research/foveation/color_math.py. */
	struct squish_look {
		double sharpen, clarity, hdr, bloom, sharpen_plus;
		double brightness, contrast, gamma, saturation, vibrance, temperature, tint, hue, highlights, shadows;
		double bw, sepia, night;
	} look;
	/* Stream uses starvation levels (key sl=1): the viewer reads the level marker, see
	 * research/foveation/STARVATION_SPEC.md. */
	bool starve;
};

bool squish_look_active(const struct squish_look *l);
bool squish_color_active(const struct squish_look *l);
#define SQUISH_LOOK_CODE_MAX 40 /* bytes incl. the terminator: mark (3) + 10 characters (3 each) + 1 */
/* Writes the look code ("" when no value is set); returns its length in bytes. */
size_t squish_look_pack(const struct squish_look *l, char *buf, size_t size);
/* Reads a look code (UTF-8, the "※" mark optional). False if it is not a valid code. */
bool squish_look_unpack(const char *s, struct squish_look *l);

/* "FSQ1:rx=..,ry=..,cx=..,cy=..,fx=..,fy=..,px=..,py=..,ar=..[,ox=..,oy=..,bw=..,bh=..][,ls=..,lc=..,lh=..,lb=..,lp=..]" */
void squish_config_format(const struct squish_config *cfg, char *buf, size_t size);
bool squish_config_parse(const char *s, struct squish_config *cfg);
