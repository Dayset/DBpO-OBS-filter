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

#include <obs-module.h>
#include <graphics/vec2.h>
#include <graphics/vec4.h>
#include <util/bmem.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <plugin-support.h>
#include <plugin-text.h>
#include "embedded-effect.h" /* generated from data/foveated_squish.effect */
#include "squish-math.h"
#include "atlas-math.h"

/* Width of the squished picture when sharp regions are on; the strip uses the rest. */
#define ATLAS_BOX_W 0.8

/* Why sharp regions are or are not streamed (shown in the settings info). */
enum atlas_state {
	ATLAS_OK = 0,
	ATLAS_NOFIT,   /* do not fit the strip above their density floor */
	ATLAS_OVERLAP, /* two regions share pixels */
	ATLAS_SIZE,    /* outside the source, or too small / large */
	ATLAS_CUSTOM,  /* Custom squeeze has no strip */
};

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif

#define S_RX "squeeze_width"
#define S_RY "squeeze_height"
#define S_CX "center_width"
#define S_CY "center_height"
#define S_FX "focus_x"
#define S_FY "focus_y"
#define S_PX "curve_x"
#define S_PY "curve_y"
#define S_LINK "same_for_y"
#define S_PREFILTER "prefilter"
#define S_CLEAN "clean_strength"
#define S_VIEW "view"
#define S_LAYOUT "layout" /* old setting, read for migration */
#define S_TARGET "stream_resolution"
#define S_MAXCOMP "max_edge_compression"
#define S_INFO "info"
#define S_CONFIG "config"
/* Sharp regions (atlas): how many, then per region a rectangle in percent of the source and a scale in percent. */
#define S_ATLAS_COUNT "atlas_count"
#define S_ATLAS_X0 "atlas_%d_x0"
#define S_ATLAS_Y0 "atlas_%d_y0"
#define S_ATLAS_X1 "atlas_%d_x1"
#define S_ATLAS_Y1 "atlas_%d_y1"
#define S_ATLAS_SCALE "atlas_%d_scale"
#define S_IMPORT "import"
/* Viewer look: sent to viewers in the config string, applied by the browser
 * extension, shown here only in the Unsquished preview (never streamed). */
#define S_LOOK_SHARPEN "look_sharpen"
#define S_LOOK_CLARITY "look_clarity"
#define S_LOOK_HDR "look_hdr_toning"
#define S_LOOK_BLOOM "look_bloom"
#define S_LOOK_SHARPEN_PLUS "look_sharpen_plus"

enum layout_mode {
	LAYOUT_FILL = 0, /* squeeze = canvas / source: the picture fills a standard canvas */
	/* 1 was "black bars": removed, the bars cost about half of the center
	 * gain. Old settings with 1 fall back to Fill. */
	LAYOUT_EXACT = 2, /* manual squeeze, output = squished size (set the canvas to it) */
};

enum fit_result {
	FIT_OK = 0,
	FIT_REDUCED = 1, /* center/curve reduced so the squeeze fits */
	FIT_LIMITED = 2, /* squeeze limited by the density floor */
};

/* Sizes for the current source, canvas and settings. */
struct layout {
	struct squish_config c; /* exact ratios for the rounded sizes */
	uint32_t w, h;          /* source */
	uint32_t ow, oh;        /* squished picture */
	uint32_t fw, fh;        /* streamed frame */
	uint32_t cw, ch;        /* OBS canvas (base) */
	uint32_t vw, vh;        /* OBS output (scaled) */
	uint32_t tw, th;        /* chosen stream resolution (Fill) */
	enum fit_result fit;
	struct atlas_slot slots[ATLAS_MAX_REGIONS]; /* sharp regions in the strip */
	int nslots;
	enum atlas_state atlas_state;
};

/* Stream resolution list: 0 = match the canvas, 1 = custom squeeze, else a
 * standard height (16:9). */
#define TARGET_CANVAS 0
#define TARGET_CUSTOM 1

static void target_dims(int target, uint32_t *tw, uint32_t *th)
{
	*th = (uint32_t)target;
	*tw = target == 480 ? 854 : (uint32_t)(target * 16.0 / 9.0 / 2.0 + 0.5) * 2;
}

enum view_mode {
	VIEW_SQUISHED = 0,   /* what gets streamed */
	VIEW_UNSQUISHED = 1, /* what extension viewers see */
	VIEW_ZONE = 2,
};

struct squish_filter {
	obs_source_t *context;
	gs_effect_t *effect;
	gs_texrender_t *input;
	gs_texrender_t *squished;
	gs_texrender_t *output;
	gs_texrender_t *cleaned;
	gs_texrender_t *look_h;    /* look: horizontal blur, half size */
	gs_texrender_t *look_v;    /* look: vertical blur, half size */
	gs_texrender_t *look_out;  /* look: Details result */
	gs_texrender_t *look_out2; /* look: Sharpen+ result */
	gs_texrender_t *display;
	gs_texrender_t *composed; /* streamed frame: picture + atlas strip */
	gs_texrender_t *mean;     /* 1x1: average colour of one region */
	bool needs_update;
	bool output_valid;

	struct squish_config cfg; /* as requested (r may be clamped, see below) */
	bool clamped;
	bool prefilter;
	double clean; /* 0..1 */
	enum view_mode view;
	enum layout_mode layout;
	int target;   /* TARGET_CANVAS, TARGET_CUSTOM or a height */
	double floor; /* lowest density = 1 / max edge compression */
	struct atlas_region regions[ATLAS_MAX_REGIONS];
	int nregions;

	/* What the properties view shows, so it only rebuilds when this changes
	 * (rebuilding while a slider is dragged makes the sliders jump). */
	char shown_key[64];
	int shown_vis;
};

/* Labels from data/locale/en-US.ini, or the copy compiled into the plugin. */
static const char *T(const char *key)
{
	return plugin_text(key);
}

static const char *fsq_get_name(void *unused)
{
	UNUSED_PARAMETER(unused);
	return T("FoveatedSquish");
}

/* ------------------------------------------------------------------ settings <-> config */

static void read_config(obs_data_t *s, struct squish_config *cfg)
{
	const bool link = obs_data_get_bool(s, S_LINK);
	cfg->x.r = obs_data_get_double(s, S_RX) / 100.0;
	cfg->x.c = obs_data_get_double(s, S_CX) / 200.0;
	cfg->x.f = obs_data_get_double(s, S_FX) / 100.0;
	cfg->x.p = obs_data_get_double(s, S_PX);
	cfg->y.r = obs_data_get_double(s, link ? S_RX : S_RY) / 100.0;
	cfg->y.c = obs_data_get_double(s, link ? S_CX : S_CY) / 200.0;
	cfg->y.f = obs_data_get_double(s, S_FY) / 100.0;
	cfg->y.p = obs_data_get_double(s, link ? S_PX : S_PY);
	cfg->look.sharpen = obs_data_get_double(s, S_LOOK_SHARPEN) / 100.0;
	cfg->look.clarity = obs_data_get_double(s, S_LOOK_CLARITY) / 100.0;
	cfg->look.hdr = obs_data_get_double(s, S_LOOK_HDR) / 100.0;
	cfg->look.bloom = obs_data_get_double(s, S_LOOK_BLOOM) / 100.0;
	cfg->look.sharpen_plus = obs_data_get_double(s, S_LOOK_SHARPEN_PLUS) / 100.0;
}

/* Keep each axis within what the curve can deliver (density >= 8%). */
static bool clamp_axis(struct squish_axis *a, double floor)
{
	if (a->p < 0.1)
		a->p = 0.1;
	if (a->r > 1.0)
		a->r = 1.0;
	const double min_r = squish_axis_min_r_floor(a, floor);
	if (a->r < min_r) {
		a->r = min_r;
		return true;
	}
	return false;
}

static uint32_t even_size(double v)
{
	uint32_t n = (uint32_t)(v / 2.0 + 0.5) * 2;
	return n < 2 ? 2 : n;
}

/* Squeeze one axis to ratio r; if the density floor forbids it, shrink the
 * sharp center first, then the curve, then give up and limit r. */
static enum fit_result fit_axis(struct squish_axis *a, double r, double floor)
{
	a->r = r > 1.0 ? 1.0 : r;
	if (a->r >= squish_axis_min_r_floor(a, floor))
		return FIT_OK;
	/* Keep the center size (the main control); flatten the curve first. */
	const double c0 = a->c, p0 = a->p;
	a->p = 0.1;
	if (a->r >= squish_axis_min_r_floor(a, floor)) {
		double lo = 0.1, hi = p0;
		for (int i = 0; i < 40; i++) {
			a->p = 0.5 * (lo + hi);
			if (a->r >= squish_axis_min_r_floor(a, floor))
				lo = a->p;
			else
				hi = a->p;
		}
		a->p = lo;
		return FIT_REDUCED;
	}
	/* Even a flat curve can't: the center itself is too big for this squeeze. */
	double lo = 0.005, hi = c0;
	for (int i = 0; i < 40; i++) {
		a->c = 0.5 * (lo + hi);
		if (a->r >= squish_axis_min_r_floor(a, floor))
			lo = a->c;
		else
			hi = a->c;
	}
	a->c = lo;
	if (a->r >= squish_axis_min_r_floor(a, floor))
		return FIT_REDUCED;
	a->r = squish_axis_min_r_floor(a, floor);
	return FIT_LIMITED;
}

static void video_sizes(struct layout *L)
{
	struct obs_video_info ovi;
	if (obs_get_video_info(&ovi) && ovi.base_width && ovi.base_height) {
		L->cw = ovi.base_width;
		L->ch = ovi.base_height;
		L->vw = ovi.output_width;
		L->vh = ovi.output_height;
	} else {
		L->cw = L->vw = L->w;
		L->ch = L->vh = L->h;
	}
}

static int read_target(obs_data_t *s)
{
	if (obs_data_has_user_value(s, S_TARGET))
		return (int)obs_data_get_int(s, S_TARGET);
	return obs_data_get_int(s, S_LAYOUT) == LAYOUT_EXACT ? TARGET_CUSTOM : TARGET_CANVAS;
}

static int read_regions(obs_data_t *s, struct atlas_region *out);

/* Sharp regions: the picture shrinks into a box on the left (ATLAS_BOX_W of the
 * stream width); the regions are packed into the strip on the right. L is left
 * unchanged when they do not fit (the regions are then not streamed). */
static enum atlas_state atlas_check(const struct atlas_region *r, int n)
{
	for (int i = 0; i < n; i++) {
		const double w = r[i].x1 - r[i].x0, h = r[i].y1 - r[i].y0;
		if (r[i].x0 < 0.0 || r[i].y0 < 0.0 || r[i].x1 > 1.0 || r[i].y1 > 1.0 || w < ATLAS_REGION_MIN ||
		    h < ATLAS_REGION_MIN || w > ATLAS_REGION_MAX || h > ATLAS_REGION_MAX)
			return ATLAS_SIZE;
		for (int k = i + 1; k < n; k++)
			if (r[i].x0 < r[k].x1 && r[k].x0 < r[i].x1 && r[i].y0 < r[k].y1 && r[k].y0 < r[i].y1)
				return ATLAS_OVERLAP;
	}
	return ATLAS_OK;
}

static void atlas_place(const struct squish_filter *f, uint32_t w, uint32_t h, struct layout *L,
			struct squish_config *c)
{
	L->atlas_state = atlas_check(f->regions, f->nregions);
	if (L->atlas_state != ATLAS_OK)
		return;
	struct atlas a;
	memset(&a, 0, sizeof(a));
	a.squish = *c;
	a.squish.boxed = true;
	a.squish.ox = 0.0;
	a.squish.oy = 0.0;
	a.squish.bw = ATLAS_BOX_W;
	a.squish.bh = 1.0;
	a.count = f->nregions;
	memcpy(a.regions, f->regions, sizeof(a.regions));
	if (!atlas_layout(&a, (int)L->tw, (int)L->th, (int)w, (int)h, L->slots)) {
		L->atlas_state = ATLAS_NOFIT;
		return;
	}
	const uint32_t pw = ((uint32_t)lround(ATLAS_BOX_W * L->tw)) & ~1u;
	L->nslots = a.count;
	L->ow = pw;
	L->oh = L->th;
	L->fw = L->tw;
	L->fh = L->th;
	c->x.r = (double)L->ow / w;
	c->y.r = (double)L->oh / h;
	c->boxed = true;
	c->ox = 0.0;
	c->oy = 0.0;
	c->bw = (double)pw / L->tw;
	c->bh = 1.0;
}

static void compute_layout(const struct squish_filter *f, uint32_t w, uint32_t h, struct layout *L)
{
	memset(L, 0, sizeof(*L));
	L->w = w;
	L->h = h;
	struct squish_config c = f->cfg;
	video_sizes(L);
	if (f->target == TARGET_CANVAS) {
		L->tw = L->cw;
		L->th = L->ch;
	} else if (f->target != TARGET_CUSTOM) {
		target_dims(f->target, &L->tw, &L->th);
	}
	if (f->layout == LAYOUT_FILL) {
		const enum fit_result fx = fit_axis(&c.x, (double)L->tw / w, f->floor);
		const enum fit_result fy = fit_axis(&c.y, (double)L->th / h, f->floor);
		L->fit = fx > fy ? fx : fy;
		L->ow = even_size(w * c.x.r);
		L->oh = even_size(h * c.y.r);
		/* Never larger than the stream resolution or the source (rounding). */
		if (L->ow > L->tw && L->fit != FIT_LIMITED)
			L->ow = L->tw;
		if (L->oh > L->th && L->fit != FIT_LIMITED)
			L->oh = L->th;
	} else {
		L->fit = f->clamped ? FIT_LIMITED : FIT_OK;
		L->ow = even_size(w * c.x.r);
		L->oh = even_size(h * c.y.r);
	}
	if (L->ow > w)
		L->ow = w;
	if (L->oh > h)
		L->oh = h;
	L->fw = L->ow; /* no bars: the streamed frame is the squished picture */
	L->fh = L->oh;
	c.x.r = (double)L->ow / w;
	c.y.r = (double)L->oh / h;
	c.ar = (double)w / h;
	c.boxed = false;
	if (f->nregions > 0) {
		if (f->layout == LAYOUT_FILL)
			atlas_place(f, w, h, L, &c);
		else
			L->atlas_state = ATLAS_CUSTOM;
	}
	L->c = c;
}

static void fsq_update(void *data, obs_data_t *s)
{
	struct squish_filter *f = data;
	read_config(s, &f->cfg);
	f->floor = 1.0 / obs_data_get_double(s, S_MAXCOMP);
	f->clamped = clamp_axis(&f->cfg.x, f->floor) | clamp_axis(&f->cfg.y, f->floor);
	f->prefilter = obs_data_get_bool(s, S_PREFILTER);
	f->clean = obs_data_get_double(s, S_CLEAN) / 100.0;
	f->view = (enum view_mode)obs_data_get_int(s, S_VIEW);
	f->target = read_target(s);
	f->layout = f->target == TARGET_CUSTOM ? LAYOUT_EXACT : LAYOUT_FILL;
	f->nregions = read_regions(s, f->regions);
}

/* ------------------------------------------------------------------ lifecycle */

static void *fsq_create(obs_data_t *settings, obs_source_t *context)
{
	struct squish_filter *f = bzalloc(sizeof(*f));
	f->context = context;

	char *path = obs_module_file("foveated_squish.effect");
	char *errors = NULL;
	obs_enter_graphics();
	if (path) {
		f->effect = gs_effect_create_from_file(path, &errors);
		if (!f->effect)
			obs_log(LOG_WARNING, "shader file '%s' failed to compile, using built-in copy: %s", path,
				errors ? errors : "(no details)");
		bfree(errors);
		errors = NULL;
	}
	if (!f->effect) {
		f->effect = gs_effect_create(fsq_embedded_effect, "foveated_squish.effect", &errors);
		if (!f->effect)
			obs_log(LOG_ERROR, "built-in shader failed to compile: %s", errors ? errors : "(no details)");
		bfree(errors);
	}
	f->input = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
	f->squished = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
	f->output = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
	f->cleaned = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
	f->look_h = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
	f->look_v = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
	f->look_out = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
	f->look_out2 = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
	f->composed = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
	f->mean = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
	obs_leave_graphics();
	bfree(path);

	if (!f->effect) {
		obs_enter_graphics();
		gs_texrender_destroy(f->input);
		gs_texrender_destroy(f->squished);
		gs_texrender_destroy(f->output);
		gs_texrender_destroy(f->cleaned);
		gs_texrender_destroy(f->look_h);
		gs_texrender_destroy(f->look_v);
		gs_texrender_destroy(f->look_out);
		gs_texrender_destroy(f->look_out2);
		gs_texrender_destroy(f->composed);
		gs_texrender_destroy(f->mean);
		obs_leave_graphics();
		bfree(f);
		return NULL;
	}
	fsq_update(f, settings);
	return f;
}

static void fsq_destroy(void *data)
{
	struct squish_filter *f = data;
	obs_enter_graphics();
	gs_texrender_destroy(f->input);
	gs_texrender_destroy(f->squished);
	gs_texrender_destroy(f->output);
	gs_texrender_destroy(f->cleaned);
	gs_texrender_destroy(f->look_h);
	gs_texrender_destroy(f->look_v);
	gs_texrender_destroy(f->look_out);
	gs_texrender_destroy(f->look_out2);
	gs_texrender_destroy(f->composed);
	gs_texrender_destroy(f->mean);
	gs_effect_destroy(f->effect);
	obs_leave_graphics();
	bfree(f);
}

static void fsq_tick(void *data, float seconds)
{
	UNUSED_PARAMETER(seconds);
	struct squish_filter *f = data;
	f->needs_update = true;
}

static bool target_size(struct squish_filter *f, uint32_t *w, uint32_t *h)
{
	obs_source_t *target = obs_filter_get_target(f->context);
	*w = target ? obs_source_get_base_width(target) : 0;
	*h = target ? obs_source_get_base_height(target) : 0;
	return *w && *h;
}

/* Output size: the streamed frame; previews use the frame size too, except
 * in Exact layout where they show the source size. */
static void output_size(struct squish_filter *f, uint32_t *ow, uint32_t *oh)
{
	uint32_t w, h;
	*ow = *oh = 0;
	if (!target_size(f, &w, &h))
		return;
	struct layout L;
	compute_layout(f, w, h, &L);
	const bool source_sized = f->view != VIEW_SQUISHED && f->layout == LAYOUT_EXACT;
	*ow = source_sized ? w : L.fw;
	*oh = source_sized ? h : L.fh;
}

static uint32_t fsq_width(void *data)
{
	uint32_t w, h;
	output_size(data, &w, &h);
	return w;
}

static uint32_t fsq_height(void *data)
{
	uint32_t w, h;
	output_size(data, &w, &h);
	return h;
}

/* ------------------------------------------------------------------ render */

static void set_axis(gs_effect_t *e, const char *a_name, const char *b_name, const struct squish_axis *a)
{
	struct squish_axis_params p;
	squish_axis_derive(a, &p);
	struct vec4 va, vb;
	vec4_set(&va, (float)p.lo, (float)p.hi, (float)p.ll, (float)p.lr);
	vec4_set(&vb, (float)p.k, (float)a->p, (float)a->r, (float)p.dmin);
	gs_effect_set_vec4(gs_effect_get_param_by_name(e, a_name), &va);
	gs_effect_set_vec4(gs_effect_get_param_by_name(e, b_name), &vb);
}

static bool run_pass(struct squish_filter *f, gs_texrender_t *target, const char *tech, gs_texture_t *src,
		     const struct squish_config *c, uint32_t sw, uint32_t sh, uint32_t ow, uint32_t oh)
{
	gs_texrender_reset(target);
	if (!gs_texrender_begin(target, ow, oh))
		return false;
	struct vec4 clear;
	vec4_zero(&clear);
	gs_clear(GS_CLEAR_COLOR, &clear, 0.0f, 0);
	gs_ortho(0.0f, (float)ow, 0.0f, (float)oh, -100.0f, 100.0f);

	gs_effect_t *e = f->effect;
	gs_effect_set_texture(gs_effect_get_param_by_name(e, "image"), src);
	struct vec2 v;
	vec2_set(&v, (float)sw, (float)sh);
	gs_effect_set_vec2(gs_effect_get_param_by_name(e, "src_size"), &v);
	vec2_set(&v, (float)ow, (float)oh);
	gs_effect_set_vec2(gs_effect_get_param_by_name(e, "out_size"), &v);
	set_axis(e, "ax_a", "ax_b", &c->x);
	set_axis(e, "ay_a", "ay_b", &c->y);
	gs_effect_set_float(gs_effect_get_param_by_name(e, "prefilter"), f->prefilter ? 1.0f : 0.0f);
	gs_effect_set_float(gs_effect_get_param_by_name(e, "clean"), (float)f->clean);
	/* Alignment uses the full/squished sizes regardless of pass direction. */
	const uint32_t fw = sw > ow ? sw : ow, fh = sh > oh ? sh : oh;
	const uint32_t qw = sw > ow ? ow : sw, qh = sh > oh ? oh : sh;
	vec2_set(&v, (float)squish_axis_align(&c->x, fw, qw), (float)squish_axis_align(&c->y, fh, qh));
	gs_effect_set_vec2(gs_effect_get_param_by_name(e, "align"), &v);

	while (gs_effect_loop(e, tech))
		gs_draw_sprite(NULL, 0, ow, oh);
	gs_texrender_end(target);
	return true;
}

/* One viewer-look pass: src (sw x sh) -> target (ow x oh). */
static bool look_pass(struct squish_filter *f, gs_texrender_t *target, const char *tech, gs_texture_t *src,
		      gs_texture_t *large, uint32_t sw, uint32_t sh, uint32_t ow, uint32_t oh, float step_x,
		      float step_y)
{
	gs_texrender_reset(target);
	if (!gs_texrender_begin(target, ow, oh))
		return false;
	struct vec4 v4;
	vec4_zero(&v4);
	gs_clear(GS_CLEAR_COLOR, &v4, 0.0f, 0);
	gs_ortho(0.0f, (float)ow, 0.0f, (float)oh, -100.0f, 100.0f);
	gs_effect_t *e = f->effect;
	const struct squish_look *l = &f->cfg.look;
	gs_effect_set_texture(gs_effect_get_param_by_name(e, "image"), src);
	gs_effect_set_texture(gs_effect_get_param_by_name(e, "large"), large ? large : src);
	struct vec2 v;
	vec2_set(&v, (float)sw, (float)sh);
	gs_effect_set_vec2(gs_effect_get_param_by_name(e, "src_size"), &v);
	vec2_set(&v, step_x, step_y);
	gs_effect_set_vec2(gs_effect_get_param_by_name(e, "blur_step"), &v);
	vec4_set(&v4, (float)l->sharpen, (float)l->clarity, (float)l->hdr, (float)l->bloom);
	gs_effect_set_vec4(gs_effect_get_param_by_name(e, "look"), &v4);
	gs_effect_set_float(gs_effect_get_param_by_name(e, "sharp_plus"), (float)l->sharpen_plus);
	while (gs_effect_loop(e, tech))
		gs_draw_sprite(NULL, 0, ow, oh);
	gs_texrender_end(target);
	return true;
}

/* Viewer look on the Unsquished preview, the same passes as the extension.
 * Blur offsets scale with the height (1080p reference), so the look matches
 * at any size. Returns the texrender holding the result. */
static gs_texrender_t *apply_look(struct squish_filter *f, gs_texrender_t *in, uint32_t w, uint32_t h)
{
	const struct squish_look *l = &f->cfg.look;
	if (!squish_look_active(l))
		return in;
	gs_texrender_t *cur = in;
	if (l->sharpen > 0.0 || l->clarity > 0.0 || l->hdr > 0.0 || l->bloom > 0.0) {
		const uint32_t hw = (w + 1) / 2, hh = (h + 1) / 2;
		double scale = h / 1080.0;
		if (scale < 0.25)
			scale = 0.25;
		gs_texture_t *src = gs_texrender_get_texture(in);
		if (!look_pass(f, f->look_h, "LookBlur", src, NULL, w, h, hw, hh, (float)(scale / w), 0.0f))
			return in;
		if (!look_pass(f, f->look_v, "LookBlur", gs_texrender_get_texture(f->look_h), NULL, hw, hh, hw, hh,
			       0.0f, (float)(scale / h)))
			return in;
		if (!look_pass(f, f->look_out, "LookDetails", src, gs_texrender_get_texture(f->look_v), w, h, w, h,
			       0.0f, 0.0f))
			return in;
		cur = f->look_out;
	}
	if (l->sharpen_plus > 0.0 &&
	    look_pass(f, f->look_out2, "LookCAS", gs_texrender_get_texture(cur), NULL, w, h, w, h, 0.0f, 0.0f))
		cur = f->look_out2;
	return cur;
}

static bool capture_input(struct squish_filter *f, obs_source_t *target, obs_source_t *parent, uint32_t w, uint32_t h)
{
	gs_texrender_reset(f->input);
	if (!gs_texrender_begin(f->input, w, h))
		return false;
	struct vec4 clear;
	vec4_zero(&clear);
	gs_clear(GS_CLEAR_COLOR, &clear, 0.0f, 0);
	gs_ortho(0.0f, (float)w, 0.0f, (float)h, -100.0f, 100.0f);
	uint32_t flags = obs_source_get_output_flags(target);
	if (target == parent && !(flags & OBS_SOURCE_CUSTOM_DRAW) && !(flags & OBS_SOURCE_ASYNC))
		obs_source_default_render(target);
	else
		obs_source_video_render(target);
	gs_texrender_end(f->input);
	return true;
}

/* One Atlas-technique quad: samples tex over uv (u0, v0, u1, v1) and draws it
 * at (x, y) in the current ortho space. qw x qh includes the gutter; the slot is
 * sw x sh inside it, and the gutter repeats the slot's edge pixels. */
static void atlas_quad(gs_effect_t *e, gs_texture_t *tex, const float uv[4], float qw, float qh, float sw, float sh,
		       float gutter, float x, float y)
{
	struct vec2 v;
	gs_effect_set_texture(gs_effect_get_param_by_name(e, "image"), tex);
	vec2_set(&v, uv[0], uv[1]);
	gs_effect_set_vec2(gs_effect_get_param_by_name(e, "atlas_uv0"), &v);
	vec2_set(&v, uv[2], uv[3]);
	gs_effect_set_vec2(gs_effect_get_param_by_name(e, "atlas_uv1"), &v);
	vec2_set(&v, qw, qh);
	gs_effect_set_vec2(gs_effect_get_param_by_name(e, "atlas_quad"), &v);
	vec2_set(&v, sw, sh);
	gs_effect_set_vec2(gs_effect_get_param_by_name(e, "atlas_slot"), &v);
	gs_effect_set_float(gs_effect_get_param_by_name(e, "atlas_gutter"), gutter);
	gs_matrix_push();
	gs_matrix_translate3f(x, y, 0.0f);
	while (gs_effect_loop(e, "Atlas"))
		gs_draw_sprite(NULL, 0, (uint32_t)qw, (uint32_t)qh);
	gs_matrix_pop();
}

/* Streamed frame: the squished picture at the origin, then each region copied
 * from the uncleaned input into its slot. */
static bool compose_atlas(struct squish_filter *f, const struct layout *L, gs_texture_t *raw, gs_texture_t *sq)
{
	gs_texrender_reset(f->composed);
	if (!gs_texrender_begin(f->composed, L->fw, L->fh))
		return false;
	struct vec4 clear;
	vec4_zero(&clear);
	gs_clear(GS_CLEAR_COLOR, &clear, 0.0f, 0);
	gs_ortho(0.0f, (float)L->fw, 0.0f, (float)L->fh, -100.0f, 100.0f);
	gs_effect_t *e = f->effect;

	const float full[4] = {0.0f, 0.0f, 1.0f, 1.0f};
	atlas_quad(e, sq, full, (float)L->ow, (float)L->oh, (float)L->ow, (float)L->oh, 0.0f, 0.0f, 0.0f);

	const float g = (float)ATLAS_GUTTER;
	for (int i = 0; i < L->nslots; i++) {
		const struct atlas_slot *s = &L->slots[i];
		const struct atlas_region *r = &f->regions[i];
		/* Same orientation as the Squish pass: uv y runs with the top-based
		 * region y, and the slot's top-based y is its ortho y. */
		const float uv[4] = {(float)r->x0, (float)r->y0, (float)r->x1, (float)r->y1};
		atlas_quad(e, raw, uv, (float)s->w + 2.0f * g, (float)s->h + 2.0f * g, (float)s->w, (float)s->h, g,
			   (float)s->x - g, (float)s->y - g);
	}
	gs_texrender_end(f->composed);
	return true;
}

/* Unsquished preview with sharp regions: paste each region back where it is in
 * the source, from its slot in the streamed frame (what viewers see). */
static bool paste_regions(struct squish_filter *f, const struct layout *L, uint32_t pw, uint32_t ph)
{
	if (!gs_texrender_begin(f->output, pw, ph))
		return false;
	gs_ortho(0.0f, (float)pw, 0.0f, (float)ph, -100.0f, 100.0f);
	gs_effect_t *e = f->effect;
	gs_texture_t *strip = gs_texrender_get_texture(f->composed);
	const float fw = (float)L->fw, fh = (float)L->fh;
	for (int i = 0; i < L->nslots; i++) {
		const struct atlas_slot *s = &L->slots[i];
		const struct atlas_region *r = &f->regions[i];
		const float uv[4] = {(float)s->x / fw, (float)s->y / fh, (float)(s->x + s->w) / fw,
				     (float)(s->y + s->h) / fh};
		const float qw = (float)((r->x1 - r->x0) * pw), qh = (float)((r->y1 - r->y0) * ph);
		atlas_quad(e, strip, uv, qw, qh, qw, qh, 0.0f, (float)(r->x0 * pw), (float)(r->y0 * ph));
	}
	gs_texrender_end(f->output);
	return true;
}

/* Blank duplicate: the region also sits in the squished picture, where it costs
 * bits twice. Paint its footprint there with the region's average colour, so the
 * encoder spends almost nothing on it. The sharp copy in the strip is unchanged. */
static void blank_duplicates(struct squish_filter *f, const struct layout *L, gs_texture_t *raw,
			     gs_texrender_t *picture)
{
	gs_effect_t *e = f->effect;
	for (int i = 0; i < L->nslots; i++) {
		const struct atlas_region *r = &f->regions[i];
		const float uv[4] = {(float)r->x0, (float)r->y0, (float)r->x1, (float)r->y1};

		/* Average colour of the region: 8x8 taps into a 1x1 target. */
		gs_texrender_reset(f->mean);
		if (!gs_texrender_begin(f->mean, 1, 1))
			continue;
		struct vec4 clear;
		vec4_zero(&clear);
		gs_clear(GS_CLEAR_COLOR, &clear, 0.0f, 0);
		gs_ortho(0.0f, 1.0f, 0.0f, 1.0f, -100.0f, 100.0f);
		gs_effect_set_texture(gs_effect_get_param_by_name(e, "image"), raw);
		struct vec2 v;
		vec2_set(&v, uv[0], uv[1]);
		gs_effect_set_vec2(gs_effect_get_param_by_name(e, "atlas_uv0"), &v);
		vec2_set(&v, uv[2], uv[3]);
		gs_effect_set_vec2(gs_effect_get_param_by_name(e, "atlas_uv1"), &v);
		while (gs_effect_loop(e, "Mean"))
			gs_draw_sprite(NULL, 0, 1, 1);
		gs_texrender_end(f->mean);
		gs_texture_t *mean = gs_texrender_get_texture(f->mean);

		/* Footprint of the region in the squished picture (picture pixels). */
		const int x0 = (int)floor(squish_forward(&L->c.x, r->x0) * L->ow);
		const int x1 = (int)ceil(squish_forward(&L->c.x, r->x1) * L->ow);
		const int y0 = (int)floor(squish_forward(&L->c.y, r->y0) * L->oh);
		const int y1 = (int)ceil(squish_forward(&L->c.y, r->y1) * L->oh);
		const int fx0 = x0 < 0 ? 0 : x0, fy0 = y0 < 0 ? 0 : y0;
		const int fx1 = x1 > (int)L->ow ? (int)L->ow : x1, fy1 = y1 > (int)L->oh ? (int)L->oh : y1;
		if (fx1 <= fx0 || fy1 <= fy0)
			continue;

		if (!gs_texrender_begin(picture, L->ow, L->oh))
			continue;
		gs_ortho(0.0f, (float)L->ow, 0.0f, (float)L->oh, -100.0f, 100.0f);
		const float full[4] = {0.0f, 0.0f, 1.0f, 1.0f};
		atlas_quad(e, mean, full, (float)(fx1 - fx0), (float)(fy1 - fy0), (float)(fx1 - fx0),
			   (float)(fy1 - fy0), 0.0f, (float)fx0, (float)fy0);
		gs_texrender_end(picture);
	}
}

static void fsq_render(void *data, gs_effect_t *unused)
{
	UNUSED_PARAMETER(unused);
	struct squish_filter *f = data;
	obs_source_t *target = obs_filter_get_target(f->context);
	obs_source_t *parent = obs_filter_get_parent(f->context);
	uint32_t w, h;
	if (!f->effect || !target || !parent || !target_size(f, &w, &h)) {
		obs_source_skip_video_filter(f->context);
		return;
	}
	struct layout L;
	compute_layout(f, w, h, &L);
	const struct squish_config *c = &L.c;
	uint32_t pw = L.fw, ph = L.fh; /* preview size */
	if (f->layout == LAYOUT_EXACT) {
		pw = w;
		ph = h;
	}

	if (f->needs_update) {
		f->output_valid = false;
		gs_blend_state_push();
		gs_blend_function(GS_BLEND_ONE, GS_BLEND_ZERO);
		if (capture_input(f, target, parent, w, h)) {
			gs_texture_t *in = gs_texrender_get_texture(f->input);
			gs_texture_t *raw = in; /* regions copy from here, never cleaned */
			if (f->clean > 0.0 && run_pass(f, f->cleaned, "Clean", in, c, w, h, w, h))
				in = gs_texrender_get_texture(f->cleaned);
			if (f->view == VIEW_ZONE) {
				f->output_valid = run_pass(f, f->output, "Zone", in, c, w, h, pw, ph);
				f->display = f->output;
			} else if (run_pass(f, f->squished, "Squish", in, c, w, h, L.ow, L.oh)) {
				gs_texture_t *sq = gs_texrender_get_texture(f->squished);
				f->display = f->squished;
				f->output_valid = true;
				if (L.nslots > 0) {
					blank_duplicates(f, &L, raw, f->squished);
					/* Sharp regions: every view shows the streamed frame (picture + strip). */
					f->output_valid = compose_atlas(f, &L, raw, sq);
					f->display = f->composed;
					if (f->output_valid && f->view == VIEW_UNSQUISHED) {
						f->output_valid =
							run_pass(f, f->output, "Unsquish", sq, c, L.ow, L.oh, pw, ph) &&
							paste_regions(f, &L, pw, ph);
						f->display = f->output_valid ? apply_look(f, f->output, pw, ph)
									     : f->output;
					}
				} else if (f->view == VIEW_UNSQUISHED) {
					f->output_valid = run_pass(f, f->output, "Unsquish", sq, c, L.ow, L.oh, pw, ph);
					f->display = f->output_valid ? apply_look(f, f->output, pw, ph) : f->output;
				}
			}
		}
		gs_blend_state_pop();
		f->needs_update = false;
	}

	gs_texture_t *tex = f->output_valid ? gs_texrender_get_texture(f->display) : NULL;
	if (!tex) {
		obs_source_skip_video_filter(f->context);
		return;
	}
	const uint32_t dw = gs_texture_get_width(tex), dh = gs_texture_get_height(tex);
	gs_effect_t *def = obs_get_base_effect(OBS_EFFECT_DEFAULT);
	gs_effect_set_texture(gs_effect_get_param_by_name(def, "image"), tex);
	gs_blend_state_push();
	gs_blend_function(GS_BLEND_ONE, GS_BLEND_INVSRCALPHA);
	while (gs_effect_loop(def, "Draw"))
		gs_draw_sprite(tex, 0, dw, dh);
	gs_blend_state_pop();
}

/* ------------------------------------------------------------------ properties */

/* Bitrate saving estimate for streaming a fraction of the pixels: "the same
 * center quality at X% less bitrate". Range over clean and grainy synthetic
 * game footage at two starved CBR budgets, x264 veryfast, 10% full-quality
 * center (research/foveation/squish_savings.py). Real games and NVENC will
 * differ; treat it as a guide. */
static const double saving_table[][3] = {
	/* pixel fraction, low, high */
	{1.0, 0.0, 0.0},    {0.5625, 0.25, 0.38}, {0.36, 0.39, 0.57},
	{0.25, 0.49, 0.69}, {0.16, 0.55, 0.76},   {0.09, 0.61, 0.84},
};

static void estimate_saving(double frac, double *lo, double *hi)
{
	const size_t n = sizeof(saving_table) / sizeof(saving_table[0]);
	*lo = saving_table[n - 1][1];
	*hi = saving_table[n - 1][2];
	if (frac >= saving_table[0][0]) {
		*lo = *hi = 0.0;
		return;
	}
	for (size_t i = 1; i < n; i++)
		if (frac >= saving_table[i][0]) {
			const double t = (frac - saving_table[i][0]) / (saving_table[i - 1][0] - saving_table[i][0]);
			*lo = saving_table[i][1] + t * (saving_table[i - 1][1] - saving_table[i][1]);
			*hi = saving_table[i][2] + t * (saving_table[i - 1][2] - saving_table[i][2]);
			return;
		}
}

/* Largest full-quality center (fraction of the frame) a flat curve allows at squeeze r. */
static double max_center(double r, double floor)
{
	const double c = 1.0 - (1.0 - r) * 1.1 / (1.0 - floor);
	return c > 0.0 ? c : 0.0;
}

/* "How much to sacrifice" table: standard canvases below the source size. */
static void append_options(char *info, size_t info_size, uint32_t w, uint32_t h, uint32_t cw, uint32_t ch, double floor)
{
	/* Standard streaming sizes only: platforms (Twitch) expect these. */
	static const uint32_t heights[] = {1440, 1080, 720, 480, 360};
	size_t n = strlen(info);
	n += (size_t)snprintf(info + n, n < info_size ? info_size - n : 0, "%s", T("OptionsHeader"));
	for (size_t i = 0; i < sizeof(heights) / sizeof(heights[0]) && n < info_size; i++) {
		const uint32_t oh = heights[i], ow = oh == 480 ? 854 : (uint32_t)(oh * 16.0 / 9.0 / 2.0 + 0.5) * 2;
		if (ow >= w && oh >= h)
			continue;
		const double rx = ow < w ? (double)ow / w : 1.0, ry = oh < h ? (double)oh / h : 1.0;
		const double cmax = max_center(rx < ry ? rx : ry, floor);
		if (cmax < 0.02)
			break;
		const double frac = rx * ry;
		double lo, hi;
		estimate_saving(frac, &lo, &hi);
		n += (size_t)snprintf(info + n, info_size - n, T("OptionRow"), ow, oh, 100.0 * frac, 100.0 * cmax,
				      100.0 * lo, 100.0 * hi, (ow == cw && oh == ch) ? T("Current") : "");
	}
}

/* Info line and (optionally) the config string for the given settings. */
/* key: what decides whether the view must be rebuilt (never changes while a
 * slider is dragged, except when crossing into another state). */
/* Regions from the settings, clamped to valid rectangles. */
static int read_regions(obs_data_t *s, struct atlas_region *out)
{
	int n = (int)obs_data_get_int(s, S_ATLAS_COUNT);
	if (n < 0)
		n = 0;
	if (n > ATLAS_MAX_REGIONS)
		n = ATLAS_MAX_REGIONS;
	int count = 0;
	char key[64];
	for (int i = 0; i < n; i++) {
		struct atlas_region r;
		snprintf(key, sizeof(key), S_ATLAS_X0, i);
		r.x0 = obs_data_get_double(s, key) / 100.0;
		snprintf(key, sizeof(key), S_ATLAS_Y0, i);
		r.y0 = obs_data_get_double(s, key) / 100.0;
		snprintf(key, sizeof(key), S_ATLAS_X1, i);
		r.x1 = obs_data_get_double(s, key) / 100.0;
		snprintf(key, sizeof(key), S_ATLAS_Y1, i);
		r.y1 = obs_data_get_double(s, key) / 100.0;
		snprintf(key, sizeof(key), S_ATLAS_SCALE, i);
		r.s = obs_data_get_double(s, key) / 100.0;
		if (r.x1 <= r.x0 || r.y1 <= r.y0)
			continue; /* empty rectangle: ignored */
		if (r.s <= 0.0 || r.s > 1.0)
			r.s = 1.0;
		out[count++] = r;
	}
	return count;
}

/* Appends the region keys (gx..gs, hx.., ix.., jx..) to a squish config string. */
static void append_regions(char *cfg, size_t cfg_size, const struct atlas_region *r, int count)
{
	const char prefixes[] = "ghij";
	size_t n = strlen(cfg);
	for (int i = 0; i < count && i < ATLAS_MAX_REGIONS; i++) {
		int w = snprintf(cfg + n, cfg_size > n ? cfg_size - n : 0,
				 ",%c%c=%.3f,%c%c=%.3f,%c%c=%.3f,%c%c=%.3f,%c%c=%.3f", prefixes[i], 'x', r[i].x0,
				 prefixes[i], 'y', r[i].y0, prefixes[i], 'z', r[i].x1, prefixes[i], 'v', r[i].y1,
				 prefixes[i], 's', r[i].s);
		if (w < 0 || (size_t)w >= cfg_size - n)
			break;
		n += (size_t)w;
	}
}

static void describe(struct squish_filter *f, obs_data_t *s, char *info, size_t info_size, char *cfg, size_t cfg_size,
		     char *key, size_t key_size)
{
	struct squish_filter tmp = *f;
	read_config(s, &tmp.cfg);
	tmp.floor = 1.0 / obs_data_get_double(s, S_MAXCOMP);
	tmp.clamped = clamp_axis(&tmp.cfg.x, tmp.floor) | clamp_axis(&tmp.cfg.y, tmp.floor);
	tmp.target = read_target(s);
	tmp.layout = tmp.target == TARGET_CUSTOM ? LAYOUT_EXACT : LAYOUT_FILL;
	tmp.view = VIEW_SQUISHED;
	tmp.nregions = read_regions(s, tmp.regions);

	uint32_t w = 1920, h = 1080;
	target_size(f, &w, &h);
	struct layout L;
	compute_layout(&tmp, w, h, &L);
	if (cfg)
		squish_config_format(&L.c, cfg, cfg_size);
	struct atlas_region regions[ATLAS_MAX_REGIONS];
	const int nregions = read_regions(s, regions);
	if (cfg && nregions > 0 && L.atlas_state == ATLAS_OK)
		append_regions(cfg, cfg_size, regions, nregions);

	char note[160] = "";
	if (L.fit == FIT_LIMITED)
		snprintf(note, sizeof(note), "%s", T("Clamped"));
	else if (L.fit == FIT_REDUCED)
		snprintf(note, sizeof(note), "%s", T("Reduced"));
	const double pixels = 100.0 * (double)L.ow * L.oh / ((double)w * h);
	if (key)
		snprintf(key, key_size, "%d %d %d %ux%u %ux%u %ux%u %.4g", tmp.target, (int)L.fit,
			 L.ow >= w && L.oh >= h, w, h, L.cw, L.ch, L.vw, L.vh, tmp.floor);
	switch (tmp.layout) {
	case LAYOUT_FILL: {
		/* Checklist for the chosen resolution, checked against OBS. */
		const char *ok = T("Ok"), *change = T("Change");
		size_t n = (size_t)snprintf(info, info_size, T("InfoTarget"), L.tw, L.th, w, h, L.tw, L.th);
		n += (size_t)snprintf(info + n, info_size - n, T("CheckCanvas"),
				      (L.cw == L.tw && L.ch == L.th) ? ok : change, L.tw, L.th, L.cw, L.ch);
		n += (size_t)snprintf(info + n, info_size - n, T("CheckOutput"),
				      (L.vw == L.tw && L.vh == L.th) ? ok : change, L.tw, L.th, L.vw, L.vh);
		n += (size_t)snprintf(info + n, info_size - n, T("CheckGame"), (w > L.tw || h > L.th) ? ok : change,
				      L.tw, L.th, w, h);
		n += (size_t)snprintf(info + n, info_size - n, "%s", T("CheckAfter"));
		if (L.ow >= w && L.oh >= h) {
			snprintf(info + n, info_size - n, "%s", T("NotSqueezing"));
		} else {
			const double r = L.c.x.r < L.c.y.r ? L.c.x.r : L.c.y.r;
			double lo, hi;
			estimate_saving(pixels / 100.0, &lo, &hi);
			snprintf(info + n, info_size - n, T("InfoResult"), pixels, 100.0 * max_center(r, tmp.floor),
				 100.0 * lo, 100.0 * hi, note);
			append_options(info, info_size, w, h, L.tw, L.th, tmp.floor);
		}
		break;
	}
	default: {
		const double r = L.c.x.r < L.c.y.r ? L.c.x.r : L.c.y.r;
		double lo, hi;
		estimate_saving(pixels / 100.0, &lo, &hi);
		snprintf(info, info_size, T("InfoExact"), w, h, L.ow, L.oh, pixels, 100.0 * max_center(r, tmp.floor),
			 100.0 * lo, 100.0 * hi, L.ow, L.oh, note);
		break;
	}
	}
	/* Sharp regions: say why they are not streamed, if they are not. */
	const char *msg = NULL;
	switch (L.atlas_state) {
	case ATLAS_NOFIT:
		msg = T("AtlasNoFit");
		break;
	case ATLAS_OVERLAP:
		msg = T("AtlasOverlap");
		break;
	case ATLAS_SIZE:
		msg = T("AtlasSize");
		break;
	case ATLAS_CUSTOM:
		msg = T("AtlasCustom");
		break;
	default:
		break;
	}
	if (msg) {
		const size_t n = strlen(info);
		if (n < info_size)
			snprintf(info + n, info_size - n, "\n\n%s", msg);
	}
}

/* Sliders stop where the chosen resolution stops: the center can't exceed
 * what the squeeze allows (flat curve), the curve can't exceed what a 1%
 * center allows. Values beyond the new limits are pulled back. */
static void limit_slider(obs_properties_t *props, obs_data_t *s, const char *name, double lo, double hi, double step)
{
	obs_property_t *pr = obs_properties_get(props, name);
	if (!pr)
		return;
	obs_property_float_set_limits(pr, lo, hi, step);
	if (obs_data_get_double(s, name) > hi)
		obs_data_set_double(s, name, hi);
}

static void set_slider_limits(struct squish_filter *f, obs_properties_t *props, obs_data_t *s, bool manual)
{
	if (manual) {
		limit_slider(props, s, S_CX, 1.0, 99.0, 0.5);
		limit_slider(props, s, S_CY, 1.0, 99.0, 0.5);
		limit_slider(props, s, S_PX, 0.1, 10.0, 0.05);
		limit_slider(props, s, S_PY, 0.1, 10.0, 0.05);
		return;
	}
	struct squish_filter tmp = *f;
	read_config(s, &tmp.cfg);
	tmp.floor = 1.0 / obs_data_get_double(s, S_MAXCOMP);
	tmp.target = read_target(s);
	tmp.layout = LAYOUT_FILL;
	uint32_t w = 1920, h = 1080;
	target_size(f, &w, &h);
	struct layout L;
	compute_layout(&tmp, w, h, &L);
	const double r[2] = {L.c.x.r, L.c.y.r};
	const char *cs[2] = {S_CX, S_CY}, *ps[2] = {S_PX, S_PY};
	for (int i = 0; i < 2; i++) {
		double cmax = floor(max_center(r[i], tmp.floor) * 200.0) / 2.0; /* % of the frame, 0.5 steps */
		if (cmax < 1.0)
			cmax = 1.0;
		double pmax = r[i] < 1.0 ? (1.0 - tmp.floor) * 0.99 / (1.0 - r[i]) - 1.0 : 10.0;
		pmax = pmax > 10.0 ? 10.0 : pmax < 0.1 ? 0.1 : floor(pmax * 20.0) / 20.0;
		limit_slider(props, s, cs[i], 1.0, cmax, 0.5);
		limit_slider(props, s, ps[i], 0.1, pmax, 0.05);
	}
}

static bool cfg_modified(obs_properties_t *props, obs_property_t *p, obs_data_t *s)
{
	UNUSED_PARAMETER(p);
	struct squish_filter *f = obs_properties_get_param(props);
	if (!f)
		return false;
	const bool link = obs_data_get_bool(s, S_LINK);
	const bool manual = read_target(s) == TARGET_CUSTOM;
	obs_property_set_visible(obs_properties_get(props, S_RX), manual);
	obs_property_set_visible(obs_properties_get(props, S_RY), manual && !link);
	obs_property_set_visible(obs_properties_get(props, S_CY), !link);
	obs_property_set_visible(obs_properties_get(props, S_PY), !link);
	char info[2048], cfg[256], key[64];
	describe(f, s, info, sizeof(info), cfg, sizeof(cfg), key, sizeof(key));
	obs_data_set_string(s, S_CONFIG, cfg);
	/* Rebuild the view only when its state changes: rebuilding on every
	 * slider tick detaches the slider being dragged. Live numbers (Exact
	 * size) refresh on the next state change or when Copy is pressed. */
	const int vis = (link ? 1 : 0) | (manual ? 2 : 0);
	if (vis == f->shown_vis && strcmp(key, f->shown_key) == 0)
		return false;
	f->shown_vis = vis;
	snprintf(f->shown_key, sizeof(f->shown_key), "%s", key);
	obs_property_set_description(obs_properties_get(props, S_INFO), info);
	set_slider_limits(f, props, s, manual);
	return true;
}

static bool copy_clicked(obs_properties_t *props, obs_property_t *p, void *data)
{
	UNUSED_PARAMETER(props);
	UNUSED_PARAMETER(p);
	struct squish_filter *f = data;
	obs_data_t *s = obs_source_get_settings(f->context);
	char info[2048], cfg[256];
	describe(f, s, info, sizeof(info), cfg, sizeof(cfg), NULL, 0);
	obs_data_set_string(s, S_CONFIG, cfg);
#ifdef _WIN32
	const int n = MultiByteToWideChar(CP_UTF8, 0, cfg, -1, NULL, 0);
	HGLOBAL mem = n > 0 ? GlobalAlloc(GMEM_MOVEABLE, (size_t)n * sizeof(wchar_t)) : NULL;
	if (mem && OpenClipboard(NULL)) {
		wchar_t *dst = GlobalLock(mem);
		MultiByteToWideChar(CP_UTF8, 0, cfg, -1, dst, n);
		GlobalUnlock(mem);
		EmptyClipboard();
		if (!SetClipboardData(CF_UNICODETEXT, mem))
			GlobalFree(mem);
		CloseClipboard();
		obs_log(LOG_INFO, "copied to clipboard: %s", cfg);
	} else if (mem) {
		GlobalFree(mem);
	}
#else
	obs_log(LOG_INFO, "config (copy from the text field): %s", cfg);
#endif
	obs_data_release(s);
	return false; /* no panel rebuild (it blinks); the clipboard has the current string */
}

static bool import_clicked(obs_properties_t *props, obs_property_t *p, void *data)
{
	UNUSED_PARAMETER(p);
	struct squish_filter *f = data;
	obs_data_t *s = obs_source_get_settings(f->context);
	struct squish_config c;
	bool ok = squish_config_parse(obs_data_get_string(s, S_IMPORT), &c);
	if (ok) {
		const bool same = fabs(c.x.r - c.y.r) < 1e-6 && fabs(c.x.c - c.y.c) < 1e-6 &&
				  fabs(c.x.p - c.y.p) < 1e-6;
		obs_data_set_bool(s, S_LINK, same);
		obs_data_set_double(s, S_RX, c.x.r * 100.0);
		obs_data_set_double(s, S_RY, c.y.r * 100.0);
		obs_data_set_double(s, S_CX, c.x.c * 200.0);
		obs_data_set_double(s, S_CY, c.y.c * 200.0);
		obs_data_set_double(s, S_FX, c.x.f * 100.0);
		obs_data_set_double(s, S_FY, c.y.f * 100.0);
		obs_data_set_double(s, S_PX, c.x.p);
		obs_data_set_double(s, S_PY, c.y.p);
		obs_data_set_double(s, S_LOOK_SHARPEN, c.look.sharpen * 100.0);
		obs_data_set_double(s, S_LOOK_CLARITY, c.look.clarity * 100.0);
		obs_data_set_double(s, S_LOOK_HDR, c.look.hdr * 100.0);
		obs_data_set_double(s, S_LOOK_BLOOM, c.look.bloom * 100.0);
		obs_data_set_double(s, S_LOOK_SHARPEN_PLUS, c.look.sharpen_plus * 100.0);
		obs_data_set_string(s, S_IMPORT, "");
		obs_source_update(f->context, s);
		f->shown_key[0] = 0; /* force one refresh */
		cfg_modified(props, NULL, s);
	} else {
		obs_log(LOG_WARNING, "could not read config string");
	}
	obs_data_release(s);
	return ok;
}

static obs_properties_t *fsq_properties(void *data)
{
	struct squish_filter *f = data;
	obs_properties_t *props = obs_properties_create_param(f, NULL);
	f->shown_key[0] = 0;
	f->shown_vis = -1;

	obs_properties_add_text(props, S_INFO, T("InfoDefault"), OBS_TEXT_INFO);

	obs_property_t *tgt =
		obs_properties_add_list(props, S_TARGET, T("Target"), OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_INT);
	obs_property_list_add_int(tgt, T("Target.Canvas"), TARGET_CANVAS);
	obs_property_list_add_int(tgt, "1920x1080 (1080p)", 1080);
	obs_property_list_add_int(tgt, "1664x936 (recommended)", 936);
	obs_property_list_add_int(tgt, "1280x720 (720p)", 720);
	obs_property_list_add_int(tgt, "854x480 (480p)", 480);
	obs_property_list_add_int(tgt, "640x360 (360p)", 360);
	obs_property_list_add_int(tgt, T("Target.Custom"), TARGET_CUSTOM);
	obs_property_set_modified_callback(tgt, cfg_modified);

	/* The settings most people touch. */
	obs_properties_t *main = obs_properties_create();
	obs_property_t *pr = obs_properties_add_bool(main, S_LINK, T("Link"));
	obs_property_set_modified_callback(pr, cfg_modified);
	const char *main_sliders[][2] = {{S_RX, "SqueezeWidth"},
					 {S_RY, "SqueezeHeight"},
					 {S_CX, "CenterWidth"},
					 {S_CY, "CenterHeight"}};
	const double main_ranges[][3] = {{10, 100, 0.5}, {10, 100, 0.5}, {1, 99, 0.5}, {1, 99, 0.5}};
	for (int i = 0; i < 4; i++) {
		pr = obs_properties_add_float_slider(main, main_sliders[i][0], T(main_sliders[i][1]), main_ranges[i][0],
						     main_ranges[i][1], main_ranges[i][2]);
		obs_property_set_modified_callback(pr, cfg_modified);
	}
	pr = obs_properties_add_float_slider(main, S_PX, T("CurveX"), 0.1, 10.0, 0.05);
	obs_property_set_modified_callback(pr, cfg_modified);
	pr = obs_properties_add_float_slider(main, S_PY, T("CurveY"), 0.1, 10.0, 0.05);
	obs_property_set_modified_callback(pr, cfg_modified);
	obs_properties_add_float_slider(main, S_CLEAN, T("Clean"), 0.0, 100.0, 1.0);
	obs_properties_add_group(props, "main", T("Main"), OBS_GROUP_NORMAL, main);

	/* Rarely needed. */
	obs_properties_t *fine = obs_properties_create();
	const char *fine_sliders[][2] = {{S_FX, "FocusX"}, {S_FY, "FocusY"}};
	for (int i = 0; i < 2; i++) {
		pr = obs_properties_add_float_slider(fine, fine_sliders[i][0], T(fine_sliders[i][1]), 1.0, 99.0, 0.5);
		obs_property_set_modified_callback(pr, cfg_modified);
	}
	pr = obs_properties_add_list(fine, S_MAXCOMP, T("MaxCompression"), OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_FLOAT);
	obs_property_list_add_float(pr, "8x", 8.0);
	obs_property_list_add_float(pr, T("Comp.Default"), 12.5);
	obs_property_list_add_float(pr, "16x", 16.0);
	obs_property_list_add_float(pr, "25x", 25.0);
	obs_property_list_add_float(pr, "50x", 50.0);
	obs_property_set_modified_callback(pr, cfg_modified);
	obs_properties_add_bool(fine, S_PREFILTER, T("Prefilter"));
	obs_properties_add_group(props, "fine", T("Fine"), OBS_GROUP_NORMAL, fine);

	obs_property_t *v =
		obs_properties_add_list(props, S_VIEW, T("View"), OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_INT);
	obs_property_list_add_int(v, T("View.Squished"), VIEW_SQUISHED);
	obs_property_list_add_int(v, T("View.Unsquished"), VIEW_UNSQUISHED);
	obs_property_list_add_int(v, T("View.Zone"), VIEW_ZONE);
	plugin_ui_add_view_warning(props, v);

	obs_properties_t *look = obs_properties_create();
	obs_properties_add_text(look, "look_info", T("LookInfo"), OBS_TEXT_INFO);
	const char *look_sliders[][2] = {{S_LOOK_SHARPEN, "LookSharpen"},
					 {S_LOOK_CLARITY, "LookClarity"},
					 {S_LOOK_HDR, "LookHdr"},
					 {S_LOOK_BLOOM, "LookBloom"},
					 {S_LOOK_SHARPEN_PLUS, "LookSharpenPlus"}};
	for (int i = 0; i < 5; i++) {
		pr = obs_properties_add_float_slider(look, look_sliders[i][0], T(look_sliders[i][1]), 0.0, 100.0, 1.0);
		obs_property_set_modified_callback(pr, cfg_modified);
	}
	obs_properties_add_group(props, "look", T("Look"), OBS_GROUP_NORMAL, look);
	/* Sharp regions (atlas): rectangles of the source kept at about 1:1 in a side strip. */
	obs_properties_t *atlas = obs_properties_create();
	obs_property_t *cnt = obs_properties_add_int_slider(atlas, S_ATLAS_COUNT, "Regions", 0, ATLAS_MAX_REGIONS, 1);
	obs_property_set_modified_callback(cnt, cfg_modified);
	obs_properties_add_text(atlas, "atlas_info", T("AtlasInfo"), OBS_TEXT_INFO);
	static const char *labels[4] = {"Left", "Top", "Right", "Bottom"};
	static const char *keys[4] = {S_ATLAS_X0, S_ATLAS_Y0, S_ATLAS_X1, S_ATLAS_Y1};
	for (int i = 0; i < ATLAS_MAX_REGIONS; i++) {
		/* OBS copies names and labels, so these buffers can be local. */
		for (int k = 0; k < 4; k++) {
			char key[64], name[64];
			snprintf(key, sizeof(key), keys[k], i);
			snprintf(name, sizeof(name), "Region %d %s", i + 1, labels[k]);
			pr = obs_properties_add_float_slider(atlas, key, name, 0.0, 100.0, 0.5);
			obs_property_set_modified_callback(pr, cfg_modified);
		}
		char key[64], name[64];
		snprintf(key, sizeof(key), S_ATLAS_SCALE, i);
		snprintf(name, sizeof(name), "Region %d scale", i + 1);
		pr = obs_properties_add_float_slider(atlas, key, name, 50.0, 100.0, 1.0);
		obs_property_set_modified_callback(pr, cfg_modified);
	}
	obs_properties_add_group(props, "atlas", "Sharp regions", OBS_GROUP_NORMAL, atlas);

	obs_properties_t *share = obs_properties_create();
	obs_properties_add_text(share, S_CONFIG, T("Config"), OBS_TEXT_DEFAULT);
	obs_properties_add_button2(share, "copy", T("Copy"), copy_clicked, f);
	obs_properties_add_text(share, S_IMPORT, T("Import"), OBS_TEXT_DEFAULT);
	obs_properties_add_button2(share, "apply_import", T("ApplyImport"), import_clicked, f);
	obs_properties_add_group(props, "share", T("Share"), OBS_GROUP_NORMAL, share);

	plugin_ui_decorate(props);
	obs_data_t *cur = obs_source_get_settings(f->context);
	plugin_ui_show(props, "view_warning", obs_data_get_int(cur, S_VIEW) != VIEW_SQUISHED);
	obs_data_release(cur);
	return props;
}

static void fsq_defaults(obs_data_t *s)
{
	/* Defaults from research/foveation/squish_profiles.py (CBR, see README). */
	obs_data_set_default_bool(s, S_LINK, true);
	obs_data_set_default_double(s, S_RX, 75.0);
	obs_data_set_default_double(s, S_RY, 75.0);
	obs_data_set_default_double(s, S_CX, 30.0);
	obs_data_set_default_double(s, S_CY, 30.0);
	obs_data_set_default_double(s, S_FX, 50.0);
	obs_data_set_default_double(s, S_FY, 50.0);
	obs_data_set_default_double(s, S_PX, 0.5);
	obs_data_set_default_double(s, S_PY, 0.5);
	obs_data_set_default_bool(s, S_PREFILTER, true);
	obs_data_set_default_double(s, S_CLEAN, 50.0);
	obs_data_set_default_int(s, S_VIEW, VIEW_SQUISHED);
	obs_data_set_default_int(s, S_ATLAS_COUNT, 0);
	for (int i = 0; i < ATLAS_MAX_REGIONS; i++) {
		char key[64];
		snprintf(key, sizeof(key), S_ATLAS_X0, i);
		obs_data_set_default_double(s, key, 0.0);
		snprintf(key, sizeof(key), S_ATLAS_Y0, i);
		obs_data_set_default_double(s, key, 0.0);
		snprintf(key, sizeof(key), S_ATLAS_X1, i);
		obs_data_set_default_double(s, key, 20.0);
		snprintf(key, sizeof(key), S_ATLAS_Y1, i);
		obs_data_set_default_double(s, key, 40.0);
		snprintf(key, sizeof(key), S_ATLAS_SCALE, i);
		obs_data_set_default_double(s, key, 100.0);
	}
	obs_data_set_default_int(s, S_TARGET, TARGET_CANVAS);
	obs_data_set_default_double(s, S_MAXCOMP, 12.5);
}

struct obs_source_info foveated_squish_filter = {
	.id = "foveated_squish_filter",
	.type = OBS_SOURCE_TYPE_FILTER,
	.output_flags = OBS_SOURCE_VIDEO,
	.get_name = fsq_get_name,
	.create = fsq_create,
	.destroy = fsq_destroy,
	.update = fsq_update,
	.video_tick = fsq_tick,
	.video_render = fsq_render,
	.get_width = fsq_width,
	.get_height = fsq_height,
	.get_properties = fsq_properties,
	.get_defaults = fsq_defaults,
};
