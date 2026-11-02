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
#include "embedded-effect.h" /* generated from data/foveated_squish.effect */
#include "squish-math.h"

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
#define S_VIEW "view"
#define S_LAYOUT "layout"
#define S_MAXCOMP "max_edge_compression"
#define S_INFO "info"
#define S_CONFIG "config"
#define S_IMPORT "import"

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
	uint32_t cw, ch;        /* canvas */
	enum fit_result fit;
};

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
	gs_texrender_t *display;
	bool needs_update;
	bool output_valid;

	struct squish_config cfg; /* as requested (r may be clamped, see below) */
	bool clamped;
	bool prefilter;
	enum view_mode view;
	enum layout_mode layout;
	double floor; /* lowest density = 1 / max edge compression */

	/* What the properties view shows, so it only rebuilds when this changes
	 * (rebuilding while a slider is dragged makes the sliders jump). */
	char shown_key[64];
	int shown_vis;
};

/* English fallbacks used when the locale file can't be found. */
static const struct {
	const char *key;
	const char *text;
} fsq_default_text[] = {
	{"FoveatedSquish", "Foveated Squish"},
	{"InfoDefault",
	 "Squeezes the edges (X and Y) so a fixed stream bitrate is spent on the sharp center. Viewers unfold it with the DBpO Unsquish browser extension."},
	{"InfoFill",
	 "Source %ux%u -> fills your %ux%u canvas: %.0f%% of the pixels are streamed. Full-quality center: up to ~%.0f%% of the frame.\nEstimated: the same center quality at ~%.0f-%.0f%% less bitrate (or, at your current bitrate, a sharper center). %s\nUse View > Squeezed zone to see the center (green) and edge squeeze (red) live."},
	{"OptionsHeader",
	 "\n\nHow much to sacrifice is set by the canvas (Settings > Video: Base and Output, the same). Smaller canvas = more saving, smaller possible center:"},
	{"OptionRow", "\n  %ux%u: %.0f%% of pixels, center up to %.0f%%, est. %.0f-%.0f%% less bitrate%s"},
	{"Current", "  <- now"},
	{"InfoFillSmall",
	 "NOT SQUEEZING: the source (%ux%u) must be larger than the canvas (%ux%u). Run the game at a higher resolution than the canvas (2560x1440 for a 1920x1080 canvas, e.g. with NVIDIA DLDSR 1.78x on a 1080p monitor), or use the Exact size layout. See the setup guide."},
	{"InfoExact",
	 "Source %ux%u -> streamed %ux%u (%.0f%% of the pixels). Full-quality center: up to ~%.0f%%. Estimated: the same center quality at ~%.0f-%.0f%% less bitrate. Set OBS Settings > Video > Base (Canvas) and Output resolution to %ux%u (you can type a custom size). These numbers update when you reopen this window. %s"},
	{"Clamped",
	 "(Squeeze limited by Max edge compression: raise it, or lower the curve or the sharp center size.)"},
	{"Reduced", "(The curve, or if needed the center, is lowered automatically so this squeeze fits.)"},
	{"MaxCompression",
	 "Max edge compression (x:1). Higher allows stronger squeeze with harder curves; above ~16 the outermost edge smears"},
	{"Layout", "Layout"},
	{"Layout.Fill", "Fill the canvas (auto squeeze = canvas / source, best)"},
	{"Layout.Exact", "Manual squeeze, exact size (set the canvas to the squished size)"},
	{"Link", "Same settings for height"},
	{"SqueezeWidth", "Streamed width (% of source)"},
	{"SqueezeHeight", "Streamed height (% of source)"},
	{"CenterWidth", "Full-quality center (% of the frame, untouched pixels)"},
	{"CenterHeight", "Full-quality center, height (% of the frame)"},
	{"FocusX", "Focus position, horizontal (%)"},
	{"FocusY", "Focus position, vertical (%)"},
	{"CurveX",
	 "Curve (low = edges squeezed evenly right from the center; high = squeeze pushed to the far edges, limited by the squeeze)"},
	{"CurveY", "Curve, vertical"},
	{"Prefilter", "Area filter when squeezing (prevents edge shimmer)"},
	{"View", "View"},
	{"View.Squished", "Squished (what gets streamed)"},
	{"View.Unsquished", "Unsquished preview (what extension viewers see)"},
	{"View.Zone", "Squeezed zone (red = squeezed harder)"},
	{"Share", "Share with viewers"},
	{"Config", "Config string as of opening this window (Copy always copies the current one)"},
	{"Copy", "Copy config to clipboard"},
	{"Import", "Paste a config string to load it"},
	{"ApplyImport", "Load pasted config"},
};

static const char *T(const char *key)
{
	const char *out = NULL;
	if (obs_module_get_string(key, &out) && out)
		return out;
	for (size_t i = 0; i < sizeof(fsq_default_text) / sizeof(fsq_default_text[0]); i++)
		if (strcmp(fsq_default_text[i].key, key) == 0)
			return fsq_default_text[i].text;
	return key;
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

static void canvas_size(uint32_t *cw, uint32_t *ch, uint32_t w, uint32_t h)
{
	struct obs_video_info ovi;
	if (obs_get_video_info(&ovi) && ovi.base_width && ovi.base_height) {
		*cw = ovi.base_width;
		*ch = ovi.base_height;
	} else {
		*cw = w;
		*ch = h;
	}
}

static void compute_layout(const struct squish_filter *f, uint32_t w, uint32_t h, struct layout *L)
{
	memset(L, 0, sizeof(*L));
	L->w = w;
	L->h = h;
	struct squish_config c = f->cfg;
	canvas_size(&L->cw, &L->ch, w, h);
	if (f->layout == LAYOUT_FILL) {
		const enum fit_result fx = fit_axis(&c.x, (double)L->cw / w, f->floor);
		const enum fit_result fy = fit_axis(&c.y, (double)L->ch / h, f->floor);
		L->fit = fx > fy ? fx : fy;
		L->ow = even_size(w * c.x.r);
		L->oh = even_size(h * c.y.r);
		/* Never larger than the canvas or the source (rounding). */
		if (L->ow > L->cw && L->fit != FIT_LIMITED)
			L->ow = L->cw;
		if (L->oh > L->ch && L->fit != FIT_LIMITED)
			L->oh = L->ch;
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
	L->c = c;
}

static void fsq_update(void *data, obs_data_t *s)
{
	struct squish_filter *f = data;
	read_config(s, &f->cfg);
	f->floor = 1.0 / obs_data_get_double(s, S_MAXCOMP);
	f->clamped = clamp_axis(&f->cfg.x, f->floor) | clamp_axis(&f->cfg.y, f->floor);
	f->prefilter = obs_data_get_bool(s, S_PREFILTER);
	f->view = (enum view_mode)obs_data_get_int(s, S_VIEW);
	f->layout = obs_data_get_int(s, S_LAYOUT) == LAYOUT_EXACT ? LAYOUT_EXACT : LAYOUT_FILL;
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
	obs_leave_graphics();
	bfree(path);

	if (!f->effect) {
		obs_enter_graphics();
		gs_texrender_destroy(f->input);
		gs_texrender_destroy(f->squished);
		gs_texrender_destroy(f->output);
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
			if (f->view == VIEW_ZONE) {
				f->output_valid = run_pass(f, f->output, "Zone", in, c, w, h, pw, ph);
				f->display = f->output;
			} else if (run_pass(f, f->squished, "Squish", in, c, w, h, L.ow, L.oh)) {
				gs_texture_t *sq = gs_texrender_get_texture(f->squished);
				f->display = f->squished;
				f->output_valid = true;
				if (f->view == VIEW_UNSQUISHED) {
					f->output_valid = run_pass(f, f->output, "Unsquish", sq, c, L.ow, L.oh, pw, ph);
					f->display = f->output;
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
	static const uint32_t heights[] = {1440, 1080, 900, 720, 540, 432, 360, 288};
	size_t n = strlen(info);
	n += (size_t)snprintf(info + n, n < info_size ? info_size - n : 0, "%s", T("OptionsHeader"));
	for (size_t i = 0; i < sizeof(heights) / sizeof(heights[0]) && n < info_size; i++) {
		const uint32_t oh = heights[i], ow = (uint32_t)(oh * 16.0 / 9.0 / 2.0 + 0.5) * 2;
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
static void describe(struct squish_filter *f, obs_data_t *s, char *info, size_t info_size, char *cfg, size_t cfg_size,
		     char *key, size_t key_size)
{
	struct squish_filter tmp = *f;
	read_config(s, &tmp.cfg);
	tmp.floor = 1.0 / obs_data_get_double(s, S_MAXCOMP);
	tmp.clamped = clamp_axis(&tmp.cfg.x, tmp.floor) | clamp_axis(&tmp.cfg.y, tmp.floor);
	tmp.layout = obs_data_get_int(s, S_LAYOUT) == LAYOUT_EXACT ? LAYOUT_EXACT : LAYOUT_FILL;
	tmp.view = VIEW_SQUISHED;

	uint32_t w = 1920, h = 1080;
	target_size(f, &w, &h);
	struct layout L;
	compute_layout(&tmp, w, h, &L);
	if (cfg)
		squish_config_format(&L.c, cfg, cfg_size);

	char note[160] = "";
	if (L.fit == FIT_LIMITED)
		snprintf(note, sizeof(note), "%s", T("Clamped"));
	else if (L.fit == FIT_REDUCED)
		snprintf(note, sizeof(note), "%s", T("Reduced"));
	const double pixels = 100.0 * (double)L.ow * L.oh / ((double)w * h);
	if (key)
		snprintf(key, key_size, "%d %d %d %ux%u %ux%u", (int)tmp.layout, (int)L.fit, L.ow >= w && L.oh >= h, w,
			 h, L.cw, L.ch);
	switch (tmp.layout) {
	case LAYOUT_FILL:
		if (L.ow >= w && L.oh >= h)
			snprintf(info, info_size, T("InfoFillSmall"), w, h, L.cw, L.ch);
		else {
			const double r = L.c.x.r < L.c.y.r ? L.c.x.r : L.c.y.r;
			double lo, hi;
			estimate_saving(pixels / 100.0, &lo, &hi);
			snprintf(info, info_size, T("InfoFill"), w, h, L.cw, L.ch, pixels,
				 100.0 * max_center(r, tmp.floor), 100.0 * lo, 100.0 * hi, note);
			append_options(info, info_size, w, h, L.cw, L.ch, tmp.floor);
		}
		break;
	default: {
		const double r = L.c.x.r < L.c.y.r ? L.c.x.r : L.c.y.r;
		double lo, hi;
		estimate_saving(pixels / 100.0, &lo, &hi);
		snprintf(info, info_size, T("InfoExact"), w, h, L.ow, L.oh, pixels, 100.0 * max_center(r, tmp.floor),
			 100.0 * lo, 100.0 * hi, L.ow, L.oh, note);
		break;
	}
	}
}

static bool cfg_modified(obs_properties_t *props, obs_property_t *p, obs_data_t *s)
{
	UNUSED_PARAMETER(p);
	struct squish_filter *f = obs_properties_get_param(props);
	if (!f)
		return false;
	const bool link = obs_data_get_bool(s, S_LINK);
	const bool manual = obs_data_get_int(s, S_LAYOUT) == LAYOUT_EXACT;
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

	obs_property_t *lay =
		obs_properties_add_list(props, S_LAYOUT, T("Layout"), OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_INT);
	obs_property_list_add_int(lay, T("Layout.Fill"), LAYOUT_FILL);
	obs_property_list_add_int(lay, T("Layout.Exact"), LAYOUT_EXACT);
	obs_property_set_modified_callback(lay, cfg_modified);

	const char *sliders[][2] = {{S_RX, "SqueezeWidth"}, {S_RY, "SqueezeHeight"}, {S_CX, "CenterWidth"},
				    {S_CY, "CenterHeight"}, {S_FX, "FocusX"},        {S_FY, "FocusY"}};
	const double ranges[][3] = {{10, 100, 0.5}, {10, 100, 0.5}, {1, 99, 0.5},
				    {1, 99, 0.5},   {1, 99, 0.5},   {1, 99, 0.5}};
	obs_property_t *pr = obs_properties_add_bool(props, S_LINK, T("Link"));
	obs_property_set_modified_callback(pr, cfg_modified);
	for (int i = 0; i < 6; i++) {
		pr = obs_properties_add_float_slider(props, sliders[i][0], T(sliders[i][1]), ranges[i][0], ranges[i][1],
						     ranges[i][2]);
		obs_property_set_modified_callback(pr, cfg_modified);
	}
	pr = obs_properties_add_float_slider(props, S_PX, T("CurveX"), 0.1, 10.0, 0.05);
	obs_property_set_modified_callback(pr, cfg_modified);
	pr = obs_properties_add_float_slider(props, S_PY, T("CurveY"), 0.1, 10.0, 0.05);
	obs_property_set_modified_callback(pr, cfg_modified);
	pr = obs_properties_add_float_slider(props, S_MAXCOMP, T("MaxCompression"), 2.0, 50.0, 0.5);
	obs_property_set_modified_callback(pr, cfg_modified);
	obs_properties_add_bool(props, S_PREFILTER, T("Prefilter"));

	obs_property_t *v =
		obs_properties_add_list(props, S_VIEW, T("View"), OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_INT);
	obs_property_list_add_int(v, T("View.Squished"), VIEW_SQUISHED);
	obs_property_list_add_int(v, T("View.Unsquished"), VIEW_UNSQUISHED);
	obs_property_list_add_int(v, T("View.Zone"), VIEW_ZONE);

	obs_properties_t *share = obs_properties_create();
	obs_properties_add_text(share, S_CONFIG, T("Config"), OBS_TEXT_DEFAULT);
	obs_properties_add_button2(share, "copy", T("Copy"), copy_clicked, f);
	obs_properties_add_text(share, S_IMPORT, T("Import"), OBS_TEXT_DEFAULT);
	obs_properties_add_button2(share, "apply_import", T("ApplyImport"), import_clicked, f);
	obs_properties_add_group(props, "share", T("Share"), OBS_GROUP_NORMAL, share);
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
	obs_data_set_default_double(s, S_PX, 1.0);
	obs_data_set_default_double(s, S_PY, 1.0);
	obs_data_set_default_bool(s, S_PREFILTER, true);
	obs_data_set_default_int(s, S_VIEW, VIEW_SQUISHED);
	obs_data_set_default_int(s, S_LAYOUT, LAYOUT_FILL);
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
