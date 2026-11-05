#include "atlas-math.h"

#include <math.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

static bool region_fields_ok(const struct atlas_region *r)
{
	return r->x0 >= 0 && r->y0 >= 0 && r->x1 <= 1 && r->y1 <= 1 && r->x0 < r->x1 && r->y0 < r->y1 &&
	       r->s > 0 && r->s <= 1;
}

bool atlas_parse(const char *s, struct atlas *a)
{
	if (!squish_config_parse(s, &a->squish))
		return false;
	a->count = 0;
	const char prefixes[] = "ghij";
	for (int i = 0; i < ATLAS_MAX_REGIONS; i++) {
		double v[5];
		int found = 0;
		const char *fields = "xyzvs";
		for (int f = 0; f < 5; f++) {
			char key[4] = {prefixes[i], fields[f], 0};
			const char *p = strstr(s, key);
			/* match only at a key boundary: start of string or after ',' or ':' */
			while (p && p != s && p[-1] != ',' && p[-1] != ':')
				p = strstr(p + 1, key);
			if (p && p[2] == '=') {
				char *end = NULL;
				v[f] = strtod(p + 3, &end);
				if (end == p + 3 || !isfinite(v[f]))
					return false;
				found++;
			}
		}
		if (found == 0)
			break;
		if (found != 5 || i != a->count)
			return false; /* partial or gapped region list */
		struct atlas_region r = {v[0], v[1], v[2], v[3], v[4]};
		if (!region_fields_ok(&r))
			return false;
		a->regions[a->count++] = r;
	}
	return true;
}

static double density_at(const struct squish_axis *ax, double x)
{
	return squish_density(ax, x);
}

double atlas_region_floor(const struct atlas *a, const struct atlas_region *r)
{
	double cx = (r->x0 + r->x1) / 2, cy = (r->y0 + r->y1) / 2;
	double dx = density_at(&a->squish.x, cx), dy = density_at(&a->squish.y, cy);
	double fx = (dx + 1) / 2, fy = (dy + 1) / 2;
	return fx > fy ? fx : fy;
}

static bool shelf(const struct atlas *a, int sx, int sy, int sw, int sh, double kw, double kh,
		  double factor, int src_w, int src_h, struct atlas_slot *slots)
{
	int cx = sx, cy = sy, row_h = 0;
	for (int i = 0; i < a->count; i++) {
		const struct atlas_region *r = &a->regions[i];
		double s = r->s * factor;
		if (s > 1)
			s = 1;
		double fl = atlas_region_floor(a, r);
		if (s < fl)
			s = fl;
		int w = (int)floor((r->x1 - r->x0) * src_w * s * kw);
		int h = (int)floor((r->y1 - r->y0) * src_h * s * kh);
		w = (w + ATLAS_SNAP - 1) / ATLAS_SNAP * ATLAS_SNAP;
		h = (h + ATLAS_SNAP - 1) / ATLAS_SNAP * ATLAS_SNAP;
		if (w < ATLAS_SNAP)
			w = ATLAS_SNAP;
		if (h < ATLAS_SNAP)
			h = ATLAS_SNAP;
		int cw = w + 2 * ATLAS_GUTTER, ch = h + 2 * ATLAS_GUTTER;
		if (cw > sw || ch > sh) {
			fprintf(stderr, "obs-headless: atlas shelf too big region %d s=%.3f fl=%.3f cw=%d ch=%d sw=%d sh=%d\n", i, s, fl, cw, ch, sw, sh);
			return false;
		}
		if (cx + cw > sx + sw) { /* new row */
			cx = sx;
			cy += row_h;
			row_h = 0;
		}
		if (cy + ch > sy + sh) {
			fprintf(stderr, "obs-headless: atlas shelf no room region %d s=%.3f cx=%d cy=%d cw=%d ch=%d\n", i, s, cx, cy, cw, ch);
			return false;
		}
		slots[i] = (struct atlas_slot){cx + ATLAS_GUTTER, cy + ATLAS_GUTTER, w, h, s};
		cx += cw;
		if (ch > row_h)
			row_h = ch;
	}
	return true;
}

bool atlas_layout(const struct atlas *a, int out_w, int out_h, int src_w, int src_h,
		  struct atlas_slot *slots)
{
	if (a->count == 0)
		return true;
	if (!a->squish.boxed)
		return false;
	double bw = a->squish.bw, bh = a->squish.bh;
	int pw = ((int)lround(bw * out_w)) & ~1;
	int ph = ((int)lround(bh * out_h)) & ~1;
	int sx, sy, sw, sh;
	if (bw < 1) {
		sx = pw, sy = 0, sw = out_w - pw, sh = out_h;
	} else if (bh < 1) {
		sx = 0, sy = ph, sw = out_w, sh = out_h - ph;
	} else {
		return false;
	}
	(void)sx;
	(void)sy;
	double kw = (double)out_w / src_w, kh = (double)out_h / src_h;
	double factor = 1.0;
	for (;;) {
		if (shelf(a, sx, sy, sw, sh, kw, kh, factor, src_w, src_h, slots))
			return true;
		factor *= 0.95;
		for (int i = 0; i < a->count; i++)
			if (factor * a->regions[i].s < atlas_region_floor(a, &a->regions[i])) {
				fprintf(stderr, "obs-headless: atlas floor region %d factor=%.3f s=%.3f floor=%.3f\n", i, factor, a->regions[i].s, atlas_region_floor(a, &a->regions[i]));
				return false;
			}
	}
}
