/*
Perceptual Color Reduction
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
#include <string.h>
#include <plugin-support.h>
#include "embedded-effect.h" /* generated from data/perceptual_color_reduction.effect */
#include "palette.h"

#define S_FLATTEN "flatten"
#define S_CHROMA "chroma_smoothing"
#define S_TEMPORAL "temporal_denoise"
#define S_HOLD "hold"
#define S_MODE "mode"
#define S_LEVELS_L "levels_lightness"
#define S_LEVELS_C "levels_chroma"
#define S_COLORS "palette_colors"
#define S_LUMA "luma_protection"
#define S_DITHER "dither"
#define S_DITHER_STRENGTH "dither_strength"
#define S_UPDATE_EVERY "palette_update_every"
#define S_PAL_SMOOTH "palette_smoothing"
#define S_SCENE_CUT "scene_change"
#define S_VIEW "view"
#define S_SOFTEN "soften_edges"
#define S_SOFTEN_STRENGTH "soften_strength"
#define S_SOFTEN_CENTER "soften_center"
#define S_SOFTEN_FEATHER "soften_feather"
#define S_SOFTEN_VERTICAL "soften_vertical"
#define S_INFO "info"

#define SAMPLE_W 160
#define SAMPLE_H 90
#define LUT_SIZE 512

enum reduce_mode {
	MODE_OFF = 0,
	MODE_POSTERIZE = 1,
	MODE_OCTREE = 2,
	MODE_KMEANS = 3,
};

enum view_mode {
	VIEW_RESULT = 0,
	VIEW_SPLIT = 1,
	VIEW_ZONE = 2,
};

struct pcr_filter {
	obs_source_t *context;
	gs_effect_t *effect;

	gs_texrender_t *input;   /* full-res copy of the source */
	gs_texrender_t *lab;     /* input converted to OKLab (16-bit float) */
	gs_texrender_t *out[2];  /* processed frames, ping-pong (history) */
	gs_texrender_t *sample;  /* SAMPLE_W x SAMPLE_H float copy for the palette */
	gs_texrender_t *lut;     /* packed 64^3 nearest-palette table */
	gs_texrender_t *compare; /* split-screen view */
	gs_texrender_t *display;
	gs_stagesurf_t *stage[2];
	gs_texture_t *palette_tex; /* 256x1 OKLab */
	int stage_idx;
	bool stage_ready[2];
	int out_idx;
	bool history_valid;
	bool needs_update;
	bool output_valid;
	uint32_t width, height;

	struct palette pal;
	bool pal_valid;
	bool lut_valid;
	float *samples; /* OKLab, SAMPLE_W * SAMPLE_H * 3 */
	float *prev_samples;
	bool have_prev_samples;
	uint64_t frame;

	/* settings */
	float flatten;
	float chroma_sigma;
	float temporal;
	float hold;
	enum reduce_mode mode;
	float step_l, step_c;
	int colors;
	float luma_weight;
	int dither_kind;
	float dither_strength;
	int update_every;
	float pal_smoothing;
	float scene_cut;
	enum view_mode view;
	bool soften;
	float soften_sigma;
	float soften_center;
	float soften_feather;
	bool soften_vertical;
};

/* English fallbacks used when the locale file can't be found. */
static const struct {
	const char *key;
	const char *text;
} pcr_default_text[] = {
	{"PerceptualColorReduction", "Perceptual Color Reduction"},
	{"Info",
	 "Prepares the picture for the encoder in OKLab (perceptual color space). Default: edge-preserving noise flattening only, the one option that lowered x264 bitrate on every test clip (up to -67% on grainy footage at high quality). Use View > Split compare to judge the look."},
	{"Bandwidth", "Bandwidth (noise and detail the encoder pays for)"},
	{"Flatten", "Flatten noise, edge-preserving (50 = measured sweet spot)"},
	{"Chroma", "Chroma smoothing (helps gradients, costs bits on grainy footage)"},
	{"Temporal", "Temporal denoise (helps static grainy scenes only)"},
	{"Hold", "Hold unchanged pixels (costs bits on slow pans)"},
	{"ColorReduction", "Color reduction (palette / posterize)"},
	{"ReduceInfo",
	 "Measured with x264: palette and posterize modes RAISE bitrate (+21% to +1100%), dithering roughly doubles it. Banding edges cost more bits than the gradients they replace. Use these for a stylized look, not to save bandwidth."},
	{"Mode", "Mode"},
	{"Mode.Off", "Off"},
	{"Mode.Posterize", "Posterize (OKLab grid)"},
	{"Mode.Octree", "Adaptive palette: octree"},
	{"Mode.KMeans", "Adaptive palette: k-means (most stable)"},
	{"LevelsL", "Posterize: lightness levels"},
	{"LevelsC", "Posterize: chroma levels"},
	{"Colors", "Palette: colors"},
	{"Luma", "Luminance protection"},
	{"Dither", "Dither"},
	{"Dither.None", "None"},
	{"Dither.Bayer4", "Bayer 4x4"},
	{"Dither.Bayer8", "Bayer 8x8"},
	{"DitherStrength", "Dither strength (%)"},
	{"UpdateEvery", "Palette: update every N frames"},
	{"PaletteSmoothing", "Palette: smoothing between updates (%)"},
	{"SceneCut", "Palette: scene-change threshold (%)"},
	{"View", "View"},
	{"View.Result", "Result"},
	{"View.Split", "Split compare (left original, right result)"},
	{"Soften", "Edge softening (foveated, viewers need nothing)"},
	{"SoftenInfo",
	 "Blurs the sides while the center stays sharp: detail where nobody looks costs bits. Measured with x264: about -1% at typical quality, up to -14% at high quality on grainy footage. Check the zone with View > Softened zone."},
	{"SoftenStrength", "Blur radius at the edges (px)"},
	{"SoftenCenter", "Sharp center width (% of frame)"},
	{"SoftenFeather", "Transition width (% of frame)"},
	{"SoftenVertical", "Also soften top and bottom"},
	{"View.Zone", "Softened zone (red tint)"},
};

static const char *T(const char *key)
{
	const char *out = NULL;
	if (obs_module_get_string(key, &out) && out)
		return out;
	for (size_t i = 0; i < sizeof(pcr_default_text) / sizeof(pcr_default_text[0]); i++)
		if (strcmp(pcr_default_text[i].key, key) == 0)
			return pcr_default_text[i].text;
	return key;
}

static const char *pcr_get_name(void *unused)
{
	UNUSED_PARAMETER(unused);
	return T("PerceptualColorReduction");
}

/* ------------------------------------------------------------------ effect params */

struct pass_inputs {
	gs_texrender_t *image;
	gs_texrender_t *history;
	gs_texrender_t *original;
	gs_texrender_t *lab;
	float texel_x, texel_y;
};

static void set_float(gs_effect_t *e, const char *name, float v)
{
	gs_eparam_t *p = gs_effect_get_param_by_name(e, name);
	if (p)
		gs_effect_set_float(p, v);
}

static void set_int(gs_effect_t *e, const char *name, int v)
{
	gs_eparam_t *p = gs_effect_get_param_by_name(e, name);
	if (p)
		gs_effect_set_int(p, v);
}

/* Always binds a valid texture: tex, else the fallback render target. */
static void set_tex(gs_effect_t *e, const char *name, gs_texture_t *tex, gs_texrender_t *fallback)
{
	gs_eparam_t *p = gs_effect_get_param_by_name(e, name);
	if (!p)
		return;
	if (!tex && fallback)
		tex = gs_texrender_get_texture(fallback);
	gs_effect_set_texture(p, tex);
}

static gs_texture_t *tex_of(gs_texrender_t *tr)
{
	return tr ? gs_texrender_get_texture(tr) : NULL;
}

/* libobs clears effect parameters after each technique, so every pass sets
 * all of them. */
static void apply_params(struct pcr_filter *f, const struct pass_inputs *in)
{
	gs_effect_t *e = f->effect;
	set_tex(e, "image", tex_of(in->image), NULL);
	set_tex(e, "history", tex_of(in->history), in->image);
	set_tex(e, "original", tex_of(in->original), in->image);
	set_tex(e, "lab_image", tex_of(in->lab), in->image);
	set_tex(e, "lut", f->lut_valid ? tex_of(f->lut) : NULL, in->image);
	gs_eparam_t *pp = gs_effect_get_param_by_name(e, "palette");
	if (pp)
		gs_effect_set_texture(pp, f->palette_tex ? f->palette_tex : tex_of(in->image));

	struct vec2 t;
	vec2_set(&t, in->texel_x, in->texel_y);
	gs_eparam_t *tp = gs_effect_get_param_by_name(e, "texel");
	if (tp)
		gs_effect_set_vec2(tp, &t);

	int mode = 0;
	if (f->mode == MODE_POSTERIZE)
		mode = 1;
	else if ((f->mode == MODE_OCTREE || f->mode == MODE_KMEANS) && f->lut_valid)
		mode = 2;
	set_int(e, "mode", mode);
	set_float(e, "step_l", f->step_l);
	set_float(e, "step_c", f->step_c);
	set_float(e, "luma_weight", f->luma_weight);
	set_int(e, "palette_size", f->pal.n);
	set_int(e, "dither_kind", f->mode == MODE_OFF ? 0 : f->dither_kind);
	set_float(e, "dither_strength", f->dither_strength);
	set_float(e, "flatten", f->flatten);
	set_float(e, "chroma_sigma", f->chroma_sigma);
	set_float(e, "temporal", f->temporal);
	set_float(e, "temporal_min", 0.25f);
	set_float(e, "hold", f->hold);
	set_float(e, "history_valid", f->history_valid ? 1.0f : 0.0f);
	set_float(e, "split", 0.5f);
	set_float(e, "soften_sigma", f->soften ? f->soften_sigma : 0.0f);
	set_float(e, "soften_center", f->soften_center);
	set_float(e, "soften_feather", f->soften_feather);
	set_float(e, "soften_vertical", f->soften_vertical ? 1.0f : 0.0f);
	set_float(e, "show_zone", f->view == VIEW_ZONE ? 1.0f : 0.0f);
}

static bool run_pass(struct pcr_filter *f, gs_texrender_t *target, const char *technique, uint32_t w, uint32_t h,
		     const struct pass_inputs *in)
{
	gs_texrender_reset(target);
	if (!gs_texrender_begin(target, w, h))
		return false;
	struct vec4 clear;
	vec4_zero(&clear);
	gs_clear(GS_CLEAR_COLOR, &clear, 0.0f, 0);
	gs_ortho(0.0f, (float)w, 0.0f, (float)h, -100.0f, 100.0f);
	apply_params(f, in);
	while (gs_effect_loop(f->effect, technique))
		gs_draw_sprite(NULL, 0, w, h);
	gs_texrender_end(target);
	return true;
}

/* ------------------------------------------------------------------ lifecycle */

static void free_textures(struct pcr_filter *f)
{
	gs_texrender_destroy(f->input);
	gs_texrender_destroy(f->lab);
	f->lab = NULL;
	gs_texrender_destroy(f->out[0]);
	gs_texrender_destroy(f->out[1]);
	gs_texrender_destroy(f->sample);
	gs_texrender_destroy(f->lut);
	gs_texrender_destroy(f->compare);
	gs_stagesurface_destroy(f->stage[0]);
	gs_stagesurface_destroy(f->stage[1]);
	gs_texture_destroy(f->palette_tex);
	f->input = f->out[0] = f->out[1] = f->sample = f->lut = f->compare = f->display = NULL;
	f->stage[0] = f->stage[1] = NULL;
	f->palette_tex = NULL;
}

static void create_textures(struct pcr_filter *f)
{
	f->input = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
	f->lab = gs_texrender_create(GS_RGBA16F, GS_ZS_NONE);
	/* 16-bit float history: the recursive temporal filter must not get
	 * stuck on 8-bit rounding. */
	f->out[0] = gs_texrender_create(GS_RGBA16F, GS_ZS_NONE);
	f->out[1] = gs_texrender_create(GS_RGBA16F, GS_ZS_NONE);
	f->sample = gs_texrender_create(GS_RGBA32F, GS_ZS_NONE);
	f->lut = gs_texrender_create(GS_RGBA16F, GS_ZS_NONE);
	f->compare = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
	f->stage[0] = gs_stagesurface_create(SAMPLE_W, SAMPLE_H, GS_RGBA32F);
	f->stage[1] = gs_stagesurface_create(SAMPLE_W, SAMPLE_H, GS_RGBA32F);
	f->palette_tex = gs_texture_create(PALETTE_MAX, 1, GS_RGBA32F, 1, NULL, GS_DYNAMIC);
}

static void pcr_update(void *data, obs_data_t *s)
{
	struct pcr_filter *f = data;
	const enum reduce_mode old_mode = f->mode;
	const int old_colors = f->colors;

	/* Slider ranges map to OKLab units (a just-noticeable difference is
	 * roughly 0.02). */
	f->flatten = 0.04f * (float)obs_data_get_double(s, S_FLATTEN) / 100.0f;
	f->chroma_sigma = 3.0f * (float)obs_data_get_double(s, S_CHROMA) / 100.0f;
	f->temporal = 0.06f * (float)obs_data_get_double(s, S_TEMPORAL) / 100.0f;
	f->hold = 0.02f * (float)obs_data_get_double(s, S_HOLD) / 100.0f;
	f->mode = (enum reduce_mode)obs_data_get_int(s, S_MODE);
	f->step_l = 1.0f / (float)obs_data_get_int(s, S_LEVELS_L);
	f->step_c = 0.64f / (float)obs_data_get_int(s, S_LEVELS_C);
	f->colors = (int)obs_data_get_int(s, S_COLORS);
	f->luma_weight = (float)obs_data_get_double(s, S_LUMA);
	f->dither_kind = (int)obs_data_get_int(s, S_DITHER);
	f->dither_strength = (float)obs_data_get_double(s, S_DITHER_STRENGTH) / 100.0f;
	f->update_every = (int)obs_data_get_int(s, S_UPDATE_EVERY);
	f->pal_smoothing = (float)obs_data_get_double(s, S_PAL_SMOOTH) / 100.0f;
	f->scene_cut = (float)obs_data_get_double(s, S_SCENE_CUT) / 100.0f;
	f->view = (enum view_mode)obs_data_get_int(s, S_VIEW);
	f->soften = obs_data_get_bool(s, S_SOFTEN);
	f->soften_sigma = (float)obs_data_get_double(s, S_SOFTEN_STRENGTH);
	f->soften_center = 0.5f * (float)obs_data_get_double(s, S_SOFTEN_CENTER) / 100.0f;
	f->soften_feather = 0.5f * (float)obs_data_get_double(s, S_SOFTEN_FEATHER) / 100.0f;
	f->soften_vertical = obs_data_get_bool(s, S_SOFTEN_VERTICAL);

	if (f->colors < 2)
		f->colors = 2;
	if (f->colors > PALETTE_MAX)
		f->colors = PALETTE_MAX;
	if (f->update_every < 1)
		f->update_every = 1;
	if (f->luma_weight < 0.5f)
		f->luma_weight = 0.5f;

	/* A different palette method or size starts from scratch. */
	if (old_mode != f->mode || old_colors != f->colors) {
		f->pal.n = 0;
		f->pal_valid = false;
		f->lut_valid = false;
	}
}

static void *pcr_create(obs_data_t *settings, obs_source_t *context)
{
	struct pcr_filter *f = bzalloc(sizeof(*f));
	f->context = context;
	f->samples = bzalloc(sizeof(float) * 3 * SAMPLE_W * SAMPLE_H);
	f->prev_samples = bzalloc(sizeof(float) * 3 * SAMPLE_W * SAMPLE_H);

	char *path = obs_module_file("perceptual_color_reduction.effect");
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
		f->effect = gs_effect_create(pcr_embedded_effect, "perceptual_color_reduction.effect", &errors);
		if (!f->effect)
			obs_log(LOG_ERROR, "built-in shader failed to compile: %s", errors ? errors : "(no details)");
		bfree(errors);
	}
	create_textures(f);
	obs_leave_graphics();
	bfree(path);

	if (!f->effect) {
		obs_enter_graphics();
		free_textures(f);
		obs_leave_graphics();
		bfree(f->samples);
		bfree(f->prev_samples);
		bfree(f);
		return NULL;
	}
	pcr_update(f, settings);
	return f;
}

static void pcr_destroy(void *data)
{
	struct pcr_filter *f = data;
	obs_enter_graphics();
	free_textures(f);
	gs_effect_destroy(f->effect);
	obs_leave_graphics();
	bfree(f->samples);
	bfree(f->prev_samples);
	bfree(f);
}

static void pcr_tick(void *data, float seconds)
{
	UNUSED_PARAMETER(seconds);
	struct pcr_filter *f = data;
	f->needs_update = true; /* process once per frame, however often it's drawn */
}

/* ------------------------------------------------------------------ palette */

/* Reads back the sample copy from the previous frame (no GPU stall), and
 * every `update_every` frames (or on a scene change) refines the palette and
 * rebuilds the LUT. */
static void update_palette(struct pcr_filter *f)
{
	struct pass_inputs sp = {.image = f->input,
				 .texel_x = 1.0f / (float)f->width,
				 .texel_y = 1.0f / (float)f->height};
	if (!run_pass(f, f->sample, "Sample", SAMPLE_W, SAMPLE_H, &sp))
		return;

	const int write = f->stage_idx, read = write ^ 1;
	gs_stage_texture(f->stage[write], gs_texrender_get_texture(f->sample));
	f->stage_ready[write] = true;
	f->stage_idx = read;
	if (!f->stage_ready[read])
		return;

	uint8_t *data = NULL;
	uint32_t linesize = 0;
	if (!gs_stagesurface_map(f->stage[read], &data, &linesize))
		return;
	const size_t n = SAMPLE_W * SAMPLE_H;
	for (int y = 0; y < SAMPLE_H; y++) {
		const float *row = (const float *)(data + (size_t)y * linesize);
		for (int x = 0; x < SAMPLE_W; x++) {
			float rgb[3] = {row[x * 4], row[x * 4 + 1], row[x * 4 + 2]};
			srgb_to_oklab(rgb, f->samples + ((size_t)y * SAMPLE_W + x) * 3);
		}
	}
	gs_stagesurface_unmap(f->stage[read]);
	f->stage_ready[read] = false;

	/* Scene change: mean OKLab difference between consecutive samples. */
	bool scene_cut = false;
	if (f->have_prev_samples) {
		double diff = 0.0;
		for (size_t i = 0; i < n; i++)
			diff += sqrt((double)oklab_dist2(f->samples + i * 3, f->prev_samples + i * 3, 1.0f));
		scene_cut = diff / (double)n > f->scene_cut;
	}
	memcpy(f->prev_samples, f->samples, sizeof(float) * 3 * n);
	f->have_prev_samples = true;

	const bool due = !f->pal_valid || scene_cut || (f->frame % (uint64_t)f->update_every) == 0;
	if (!due)
		return;

	if (f->mode == MODE_OCTREE) {
		struct palette fresh;
		palette_octree(f->samples, n, f->colors, &fresh);
		if (f->pal_valid && !scene_cut)
			palette_stabilize(&f->pal, &fresh, f->pal_smoothing, f->luma_weight);
		f->pal = fresh;
	} else {
		/* Warm-started k-means: one Lloyd step per update keeps the palette
		 * stable; a new scene gets several. */
		const bool warm = f->pal_valid && f->pal.n == f->colors && !scene_cut;
		palette_kmeans(f->samples, n, f->colors, warm ? 1 : 6, f->luma_weight, warm ? &f->pal : NULL, &f->pal);
	}
	f->pal_valid = f->pal.n > 0;
	if (!f->pal_valid)
		return;

	float upload[PALETTE_MAX * 4] = {0};
	for (int i = 0; i < f->pal.n; i++) {
		upload[i * 4 + 0] = f->pal.c[i][0];
		upload[i * 4 + 1] = f->pal.c[i][1];
		upload[i * 4 + 2] = f->pal.c[i][2];
		upload[i * 4 + 3] = 1.0f;
	}
	gs_texture_set_image(f->palette_tex, (const uint8_t *)upload, PALETTE_MAX * 4 * sizeof(float), false);

	struct pass_inputs lp = {.image = f->input, .texel_x = 1.0f / LUT_SIZE, .texel_y = 1.0f / LUT_SIZE};
	f->lut_valid = run_pass(f, f->lut, "BuildLUT", LUT_SIZE, LUT_SIZE, &lp);
}

/* ------------------------------------------------------------------ render */

static bool capture_input(struct pcr_filter *f, obs_source_t *target, obs_source_t *parent)
{
	gs_texrender_reset(f->input);
	if (!gs_texrender_begin(f->input, f->width, f->height))
		return false;
	struct vec4 clear;
	vec4_zero(&clear);
	gs_clear(GS_CLEAR_COLOR, &clear, 0.0f, 0);
	gs_ortho(0.0f, (float)f->width, 0.0f, (float)f->height, -100.0f, 100.0f);
	uint32_t flags = obs_source_get_output_flags(target);
	if (target == parent && !(flags & OBS_SOURCE_CUSTOM_DRAW) && !(flags & OBS_SOURCE_ASYNC))
		obs_source_default_render(target);
	else
		obs_source_video_render(target);
	gs_texrender_end(f->input);
	return true;
}

static void process_frame(struct pcr_filter *f, obs_source_t *target, obs_source_t *parent)
{
	f->output_valid = false;
	if (!capture_input(f, target, parent))
		return;

	if (f->mode == MODE_OCTREE || f->mode == MODE_KMEANS)
		update_palette(f);

	const float tx = 1.0f / (float)f->width, ty = 1.0f / (float)f->height;
	struct pass_inputs lp = {.image = f->input, .texel_x = tx, .texel_y = ty};
	if (!run_pass(f, f->lab, "ToLab", f->width, f->height, &lp))
		return;

	const int prev = f->out_idx, next = prev ^ 1;
	struct pass_inputs pp = {.image = f->input,
				 .lab = f->lab,
				 .history = f->out[prev],
				 .texel_x = tx,
				 .texel_y = ty};
	if (!run_pass(f, f->out[next], "Process", f->width, f->height, &pp))
		return;
	f->out_idx = next;
	f->history_valid = true;
	f->display = f->out[next];

	if (f->view == VIEW_SPLIT) {
		struct pass_inputs cp = {.image = f->out[next], .original = f->input, .texel_x = tx, .texel_y = ty};
		if (run_pass(f, f->compare, "Compare", f->width, f->height, &cp))
			f->display = f->compare;
	}
	f->frame++;
	f->output_valid = true;
}

static void pcr_render(void *data, gs_effect_t *unused)
{
	UNUSED_PARAMETER(unused);
	struct pcr_filter *f = data;

	obs_source_t *target = obs_filter_get_target(f->context);
	obs_source_t *parent = obs_filter_get_parent(f->context);
	uint32_t w = target ? obs_source_get_base_width(target) : 0;
	uint32_t h = target ? obs_source_get_base_height(target) : 0;
	if (!f->effect || !target || !parent || !w || !h) {
		obs_source_skip_video_filter(f->context);
		return;
	}
	if (w != f->width || h != f->height) {
		f->width = w;
		f->height = h;
		f->history_valid = false;
		f->needs_update = true;
	}

	if (f->needs_update) {
		gs_blend_state_push();
		gs_blend_function(GS_BLEND_ONE, GS_BLEND_ZERO);
		process_frame(f, target, parent);
		gs_blend_state_pop();
		f->needs_update = false;
	}

	gs_texture_t *tex = f->output_valid ? gs_texrender_get_texture(f->display) : NULL;
	if (!tex) {
		obs_source_skip_video_filter(f->context);
		return;
	}
	gs_effect_t *def = obs_get_base_effect(OBS_EFFECT_DEFAULT);
	gs_effect_set_texture(gs_effect_get_param_by_name(def, "image"), tex);
	gs_blend_state_push();
	gs_blend_function(GS_BLEND_ONE, GS_BLEND_INVSRCALPHA);
	while (gs_effect_loop(def, "Draw"))
		gs_draw_sprite(tex, 0, f->width, f->height);
	gs_blend_state_pop();
}

/* ------------------------------------------------------------------ properties */

static obs_properties_t *pcr_properties(void *data)
{
	UNUSED_PARAMETER(data);
	obs_properties_t *props = obs_properties_create();
	obs_properties_add_text(props, S_INFO, T("Info"), OBS_TEXT_INFO);

	obs_properties_t *bw = obs_properties_create();
	obs_properties_add_float_slider(bw, S_FLATTEN, T("Flatten"), 0.0, 100.0, 1.0);
	obs_properties_add_float_slider(bw, S_CHROMA, T("Chroma"), 0.0, 100.0, 1.0);
	obs_properties_add_float_slider(bw, S_TEMPORAL, T("Temporal"), 0.0, 100.0, 1.0);
	obs_properties_add_float_slider(bw, S_HOLD, T("Hold"), 0.0, 100.0, 1.0);
	obs_properties_add_group(props, "bandwidth", T("Bandwidth"), OBS_GROUP_NORMAL, bw);

	obs_properties_t *sg = obs_properties_create();
	obs_properties_add_text(sg, "soften_info", T("SoftenInfo"), OBS_TEXT_INFO);
	obs_properties_add_float_slider(sg, S_SOFTEN_STRENGTH, T("SoftenStrength"), 0.5, 6.0, 0.1);
	obs_properties_add_float_slider(sg, S_SOFTEN_CENTER, T("SoftenCenter"), 10.0, 90.0, 1.0);
	obs_properties_add_float_slider(sg, S_SOFTEN_FEATHER, T("SoftenFeather"), 5.0, 90.0, 1.0);
	obs_properties_add_bool(sg, S_SOFTEN_VERTICAL, T("SoftenVertical"));
	obs_properties_add_group(props, S_SOFTEN, T("Soften"), OBS_GROUP_CHECKABLE, sg);

	obs_properties_t *cr = obs_properties_create();
	obs_properties_add_text(cr, "reduce_info", T("ReduceInfo"), OBS_TEXT_INFO);
	obs_property_t *m = obs_properties_add_list(cr, S_MODE, T("Mode"), OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_INT);
	obs_property_list_add_int(m, T("Mode.Off"), MODE_OFF);
	obs_property_list_add_int(m, T("Mode.Posterize"), MODE_POSTERIZE);
	obs_property_list_add_int(m, T("Mode.Octree"), MODE_OCTREE);
	obs_property_list_add_int(m, T("Mode.KMeans"), MODE_KMEANS);
	obs_properties_add_int_slider(cr, S_LEVELS_L, T("LevelsL"), 8, 256, 1);
	obs_properties_add_int_slider(cr, S_LEVELS_C, T("LevelsC"), 4, 128, 1);
	obs_properties_add_int_slider(cr, S_COLORS, T("Colors"), 16, PALETTE_MAX, 1);
	obs_properties_add_float_slider(cr, S_LUMA, T("Luma"), 1.0, 3.0, 0.05);
	obs_property_t *d =
		obs_properties_add_list(cr, S_DITHER, T("Dither"), OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_INT);
	obs_property_list_add_int(d, T("Dither.None"), 0);
	obs_property_list_add_int(d, T("Dither.Bayer4"), 1);
	obs_property_list_add_int(d, T("Dither.Bayer8"), 2);
	obs_properties_add_float_slider(cr, S_DITHER_STRENGTH, T("DitherStrength"), 0.0, 100.0, 1.0);
	obs_properties_add_int_slider(cr, S_UPDATE_EVERY, T("UpdateEvery"), 1, 30, 1);
	obs_properties_add_float_slider(cr, S_PAL_SMOOTH, T("PaletteSmoothing"), 0.0, 95.0, 1.0);
	obs_properties_add_float_slider(cr, S_SCENE_CUT, T("SceneCut"), 1.0, 50.0, 0.5);
	obs_properties_add_group(props, "color_reduction", T("ColorReduction"), OBS_GROUP_NORMAL, cr);

	obs_property_t *v =
		obs_properties_add_list(props, S_VIEW, T("View"), OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_INT);
	obs_property_list_add_int(v, T("View.Result"), VIEW_RESULT);
	obs_property_list_add_int(v, T("View.Split"), VIEW_SPLIT);
	obs_property_list_add_int(v, T("View.Zone"), VIEW_ZONE);
	return props;
}

static void pcr_defaults(obs_data_t *s)
{
	/* Defaults come from tests/bitrate_harness.py (see README): edge-preserving
	 * flattening at 0.02 OKLab was the only option that lowered x264 bitrate on
	 * every clip type; the rest cost bits on at least one, so they start off. */
	obs_data_set_default_double(s, S_FLATTEN, 50.0); /* 0.02 OKLab */
	obs_data_set_default_double(s, S_CHROMA, 0.0);
	obs_data_set_default_double(s, S_TEMPORAL, 0.0);
	obs_data_set_default_double(s, S_HOLD, 0.0);
	obs_data_set_default_int(s, S_MODE, MODE_OFF);
	obs_data_set_default_int(s, S_LEVELS_L, 96);
	obs_data_set_default_int(s, S_LEVELS_C, 32);
	obs_data_set_default_int(s, S_COLORS, 128);
	obs_data_set_default_double(s, S_LUMA, 1.0);
	obs_data_set_default_int(s, S_DITHER, 0);
	obs_data_set_default_double(s, S_DITHER_STRENGTH, 50.0);
	obs_data_set_default_int(s, S_UPDATE_EVERY, 3);
	obs_data_set_default_double(s, S_PAL_SMOOTH, 80.0);
	obs_data_set_default_double(s, S_SCENE_CUT, 15.0);
	obs_data_set_default_int(s, S_VIEW, VIEW_RESULT);
	obs_data_set_default_bool(s, S_SOFTEN, false);
	obs_data_set_default_double(s, S_SOFTEN_STRENGTH, 2.0);
	obs_data_set_default_double(s, S_SOFTEN_CENTER, 30.0);
	obs_data_set_default_double(s, S_SOFTEN_FEATHER, 40.0);
	obs_data_set_default_bool(s, S_SOFTEN_VERTICAL, false);
}

struct obs_source_info perceptual_color_reduction_filter = {
	.id = "perceptual_color_reduction_filter",
	.type = OBS_SOURCE_TYPE_FILTER,
	.output_flags = OBS_SOURCE_VIDEO,
	.get_name = pcr_get_name,
	.create = pcr_create,
	.destroy = pcr_destroy,
	.update = pcr_update,
	.video_tick = pcr_tick,
	.video_render = pcr_render,
	.get_properties = pcr_properties,
	.get_defaults = pcr_defaults,
};
