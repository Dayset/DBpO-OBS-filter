/*
Directional Object Blur
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
#include <plugin-support.h>

#define S_STRENGTH "strength"
#define S_MAX_RADIUS "max_blur_radius"
#define S_DOWNSCALE "flow_downscale"
#define S_SEARCH "search_radius"
#define S_SENSITIVITY "object_sensitivity"
#define S_COHERENCE "coherence_radius"
#define S_MIN_MOTION "min_motion"
#define S_STATIC_BIAS "static_bias"
#define S_SAMPLES "samples"
#define S_VIEW "view"

enum view_mode {
	VIEW_RESULT = 0,
	VIEW_VECTORS = 1,
};

struct dob_filter {
	obs_source_t *context;
	gs_effect_t *effect;

	gs_texrender_t *input;      /* full-res copy of the source */
	gs_texrender_t *small[2];   /* flow-res color, ping-pong current/previous */
	gs_texrender_t *flow;       /* raw block-matching motion */
	gs_texrender_t *coherent;   /* motion smoothed within color regions */
	gs_texrender_t *neighbor;   /* neighbor-max motion */
	gs_texrender_t *output;     /* cached final frame */

	int cur;                    /* index into small[] holding the newest frame */
	bool have_prev;
	bool needs_update;
	bool output_valid;

	uint32_t width, height;
	uint32_t flow_w, flow_h;

	/* settings */
	float strength;
	float max_radius;
	int downscale;
	int search_radius;
	float sensitivity;
	int coherence_radius;
	float min_motion;
	float static_bias;
	int samples;
	enum view_mode view;
};

static const char *dob_get_name(void *unused)
{
	UNUSED_PARAMETER(unused);
	return obs_module_text("DirectionalObjectBlur");
}

/* ------------------------------------------------------------------ helpers */

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

static void set_vec2(gs_effect_t *e, const char *name, float x, float y)
{
	gs_eparam_t *p = gs_effect_get_param_by_name(e, name);
	if (p) {
		struct vec2 v;
		vec2_set(&v, x, y);
		gs_effect_set_vec2(p, &v);
	}
}

static void set_tex(gs_effect_t *e, const char *name, gs_texrender_t *tr)
{
	gs_eparam_t *p = gs_effect_get_param_by_name(e, name);
	if (p)
		gs_effect_set_texture(p, tr ? gs_texrender_get_texture(tr) : NULL);
}

/* Draws a full-target quad with the given technique into `target`. */
static bool run_pass(struct dob_filter *f, gs_texrender_t *target, const char *technique, uint32_t w, uint32_t h)
{
	gs_texrender_reset(target);
	if (!gs_texrender_begin(target, w, h))
		return false;

	struct vec4 clear;
	vec4_zero(&clear);
	gs_clear(GS_CLEAR_COLOR, &clear, 0.0f, 0);
	gs_ortho(0.0f, (float)w, 0.0f, (float)h, -100.0f, 100.0f);

	set_vec2(f->effect, "out_texel", 1.0f / (float)w, 1.0f / (float)h);

	while (gs_effect_loop(f->effect, technique))
		gs_draw_sprite(NULL, 0, w, h);

	gs_texrender_end(target);
	return true;
}

static void free_textures(struct dob_filter *f)
{
	gs_texrender_destroy(f->input);
	gs_texrender_destroy(f->small[0]);
	gs_texrender_destroy(f->small[1]);
	gs_texrender_destroy(f->flow);
	gs_texrender_destroy(f->coherent);
	gs_texrender_destroy(f->neighbor);
	gs_texrender_destroy(f->output);
	f->input = f->small[0] = f->small[1] = NULL;
	f->flow = f->coherent = f->neighbor = f->output = NULL;
}

static void create_textures(struct dob_filter *f)
{
	f->input = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
	f->output = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
	f->small[0] = gs_texrender_create(GS_RGBA16F, GS_ZS_NONE);
	f->small[1] = gs_texrender_create(GS_RGBA16F, GS_ZS_NONE);
	f->flow = gs_texrender_create(GS_RGBA16F, GS_ZS_NONE);
	f->coherent = gs_texrender_create(GS_RGBA16F, GS_ZS_NONE);
	f->neighbor = gs_texrender_create(GS_RGBA16F, GS_ZS_NONE);
}

/* ------------------------------------------------------------------ lifecycle */

static void dob_update(void *data, obs_data_t *settings)
{
	struct dob_filter *f = data;
	int old_downscale = f->downscale;

	f->strength = (float)obs_data_get_double(settings, S_STRENGTH);
	f->max_radius = (float)obs_data_get_double(settings, S_MAX_RADIUS);
	f->downscale = (int)obs_data_get_int(settings, S_DOWNSCALE);
	f->search_radius = (int)obs_data_get_int(settings, S_SEARCH);
	f->sensitivity = (float)obs_data_get_double(settings, S_SENSITIVITY);
	f->coherence_radius = (int)obs_data_get_int(settings, S_COHERENCE);
	f->min_motion = (float)obs_data_get_double(settings, S_MIN_MOTION);
	f->static_bias = (float)obs_data_get_double(settings, S_STATIC_BIAS);
	f->samples = (int)obs_data_get_int(settings, S_SAMPLES);
	f->view = (enum view_mode)obs_data_get_int(settings, S_VIEW);

	if (f->downscale < 1)
		f->downscale = 4;

	/* A new flow resolution invalidates the previous frame. */
	if (old_downscale != f->downscale)
		f->have_prev = false;
}

static void *dob_create(obs_data_t *settings, obs_source_t *context)
{
	struct dob_filter *f = bzalloc(sizeof(*f));
	f->context = context;

	char *path = obs_module_file("directional_object_blur.effect");
	obs_enter_graphics();
	if (path) {
		char *errors = NULL;
		f->effect = gs_effect_create_from_file(path, &errors);
		if (!f->effect)
			obs_log(LOG_ERROR, "failed to load effect '%s': %s", path, errors ? errors : "(no details)");
		bfree(errors);
	}
	create_textures(f);
	obs_leave_graphics();
	bfree(path);

	if (!f->effect) {
		obs_enter_graphics();
		free_textures(f);
		obs_leave_graphics();
		bfree(f);
		return NULL;
	}

	dob_update(f, settings);
	return f;
}

static void dob_destroy(void *data)
{
	struct dob_filter *f = data;
	obs_enter_graphics();
	free_textures(f);
	gs_effect_destroy(f->effect);
	obs_leave_graphics();
	bfree(f);
}

static void dob_tick(void *data, float seconds)
{
	UNUSED_PARAMETER(seconds);
	struct dob_filter *f = data;
	/* video_render can run several times per frame (preview, program,
	 * projectors). Only advance the motion history once per frame. */
	f->needs_update = true;
}

/* ------------------------------------------------------------------ rendering */

static bool capture_input(struct dob_filter *f, obs_source_t *target, obs_source_t *parent)
{
	gs_texrender_reset(f->input);
	if (!gs_texrender_begin(f->input, f->width, f->height))
		return false;

	struct vec4 clear;
	vec4_zero(&clear);
	gs_clear(GS_CLEAR_COLOR, &clear, 0.0f, 0);
	gs_ortho(0.0f, (float)f->width, 0.0f, (float)f->height, -100.0f, 100.0f);

	uint32_t flags = obs_source_get_output_flags(target);
	bool custom_draw = (flags & OBS_SOURCE_CUSTOM_DRAW) != 0;
	bool async = (flags & OBS_SOURCE_ASYNC) != 0;

	if (target == parent && !custom_draw && !async)
		obs_source_default_render(target);
	else
		obs_source_video_render(target);

	gs_texrender_end(f->input);
	return true;
}

static void process_frame(struct dob_filter *f)
{
	gs_effect_t *e = f->effect;
	const float scale = (float)f->width / (float)f->flow_w;
	const float fw = (float)f->width, fh = (float)f->height;
	const float sw = (float)f->flow_w, sh = (float)f->flow_h;

	f->cur ^= 1;
	gs_texrender_t *cur_small = f->small[f->cur];
	gs_texrender_t *prev_small = f->small[f->cur ^ 1];

	/* 1. Downsample current frame to flow resolution. */
	set_tex(e, "image", f->input);
	set_vec2(e, "texel", 1.0f / fw, 1.0f / fh);
	set_float(e, "flow_scale", scale);
	run_pass(f, cur_small, "Downsample", f->flow_w, f->flow_h);

	/* 2. Block-matching optical flow against the previous frame. */
	set_tex(e, "image", cur_small);
	set_tex(e, "prev_image", f->have_prev ? prev_small : cur_small);
	set_vec2(e, "texel", 1.0f / sw, 1.0f / sh);
	set_int(e, "search_radius", f->have_prev ? f->search_radius : 0);
	set_float(e, "static_bias", f->static_bias);
	run_pass(f, f->flow, "Flow", f->flow_w, f->flow_h);
	f->have_prev = true;

	/* 3. Pull motion together within same-colored regions. */
	float sens = f->sensitivity / 100.0f;
	set_tex(e, "image", f->flow);
	set_tex(e, "color_small", cur_small);
	set_int(e, "coherence_radius", f->coherence_radius);
	set_float(e, "color_sigma", 5.0f + sens * sens * 400.0f);
	run_pass(f, f->coherent, "Coherence", f->flow_w, f->flow_h);

	/* 4. Neighbor max, wide enough to cover the longest allowed blur. */
	int nr = (int)ceilf((f->max_radius * 0.5f) / scale);
	if (nr < 1)
		nr = 1;
	if (nr > 8)
		nr = 8;
	set_tex(e, "image", f->coherent);
	set_int(e, "neighbor_radius", nr);
	run_pass(f, f->neighbor, "NeighborMax", f->flow_w, f->flow_h);

	/* 5. Final gather at full resolution (or the debug view). */
	set_tex(e, "image", f->input);
	set_tex(e, "motion", f->coherent);
	set_tex(e, "motion_max", f->neighbor);
	set_float(e, "shutter", f->strength);
	set_float(e, "max_radius", f->max_radius);
	set_float(e, "min_motion", f->min_motion);
	set_int(e, "sample_count", f->samples);
	set_float(e, "debug_range", (float)f->search_radius * scale);

	const char *tech = f->view == VIEW_VECTORS ? "DebugVectors" : "Blur";
	f->output_valid = run_pass(f, f->output, tech, f->width, f->height);
}

static void dob_render(void *data, gs_effect_t *unused)
{
	UNUSED_PARAMETER(unused);
	struct dob_filter *f = data;

	obs_source_t *target = obs_filter_get_target(f->context);
	obs_source_t *parent = obs_filter_get_parent(f->context);
	uint32_t w = target ? obs_source_get_base_width(target) : 0;
	uint32_t h = target ? obs_source_get_base_height(target) : 0;

	if (!f->effect || !target || !parent || !w || !h) {
		obs_source_skip_video_filter(f->context);
		return;
	}

	uint32_t fw = (w + f->downscale - 1) / f->downscale;
	uint32_t fh = (h + f->downscale - 1) / f->downscale;
	if (w != f->width || h != f->height || fw != f->flow_w || fh != f->flow_h) {
		f->width = w;
		f->height = h;
		f->flow_w = fw;
		f->flow_h = fh;
		f->have_prev = false;
		f->needs_update = true;
	}

	if (f->needs_update || !f->output_valid) {
		gs_blend_state_push();
		gs_blend_function(GS_BLEND_ONE, GS_BLEND_ZERO);

		if (capture_input(f, target, parent))
			process_frame(f);

		gs_blend_state_pop();
		f->needs_update = false;
	}

	if (!f->output_valid) {
		obs_source_skip_video_filter(f->context);
		return;
	}

	gs_effect_t *def = obs_get_base_effect(OBS_EFFECT_DEFAULT);
	gs_texture_t *tex = gs_texrender_get_texture(f->output);
	gs_eparam_t *image = gs_effect_get_param_by_name(def, "image");
	gs_effect_set_texture(image, tex);

	gs_blend_state_push();
	gs_blend_function(GS_BLEND_ONE, GS_BLEND_INVSRCALPHA);
	while (gs_effect_loop(def, "Draw"))
		gs_draw_sprite(tex, 0, f->width, f->height);
	gs_blend_state_pop();
}

/* ------------------------------------------------------------------ properties */

static obs_properties_t *dob_properties(void *data)
{
	UNUSED_PARAMETER(data);
	obs_properties_t *props = obs_properties_create();

	obs_properties_add_float_slider(props, S_STRENGTH, obs_module_text("Strength"), 0.0, 2.0, 0.05);
	obs_properties_add_float_slider(props, S_MAX_RADIUS, obs_module_text("MaxBlurRadius"), 1.0, 128.0, 1.0);
	obs_properties_add_float_slider(props, S_SENSITIVITY, obs_module_text("ObjectSensitivity"), 0.0, 100.0, 1.0);
	obs_properties_add_int_slider(props, S_SAMPLES, obs_module_text("Samples"), 4, 32, 1);

	obs_properties_t *adv = obs_properties_create();
	obs_property_t *ds = obs_properties_add_list(adv, S_DOWNSCALE, obs_module_text("FlowResolution"),
						     OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_INT);
	obs_property_list_add_int(ds, obs_module_text("FlowResolution.Half"), 2);
	obs_property_list_add_int(ds, obs_module_text("FlowResolution.Quarter"), 4);
	obs_property_list_add_int(ds, obs_module_text("FlowResolution.Eighth"), 8);
	obs_properties_add_int_slider(adv, S_SEARCH, obs_module_text("SearchRadius"), 1, 8, 1);
	obs_properties_add_int_slider(adv, S_COHERENCE, obs_module_text("CoherenceRadius"), 0, 4, 1);
	obs_properties_add_float_slider(adv, S_MIN_MOTION, obs_module_text("MinMotion"), 0.0, 8.0, 0.1);
	obs_properties_add_float_slider(adv, S_STATIC_BIAS, obs_module_text("StaticBias"), 0.0, 1.0, 0.01);
	obs_properties_add_group(props, "advanced", obs_module_text("Advanced"), OBS_GROUP_NORMAL, adv);

	obs_property_t *view = obs_properties_add_list(props, S_VIEW, obs_module_text("View"), OBS_COMBO_TYPE_LIST,
						       OBS_COMBO_FORMAT_INT);
	obs_property_list_add_int(view, obs_module_text("View.Result"), VIEW_RESULT);
	obs_property_list_add_int(view, obs_module_text("View.Vectors"), VIEW_VECTORS);

	return props;
}

static void dob_defaults(obs_data_t *settings)
{
	obs_data_set_default_double(settings, S_STRENGTH, 0.5); /* 180-degree shutter */
	obs_data_set_default_double(settings, S_MAX_RADIUS, 32.0);
	obs_data_set_default_int(settings, S_DOWNSCALE, 4);
	obs_data_set_default_int(settings, S_SEARCH, 4);
	obs_data_set_default_double(settings, S_SENSITIVITY, 50.0);
	obs_data_set_default_int(settings, S_COHERENCE, 2);
	obs_data_set_default_double(settings, S_MIN_MOTION, 1.0);
	obs_data_set_default_double(settings, S_STATIC_BIAS, 0.08);
	obs_data_set_default_int(settings, S_SAMPLES, 12);
	obs_data_set_default_int(settings, S_VIEW, VIEW_RESULT);
}

struct obs_source_info directional_object_blur_filter = {
	.id = "directional_object_blur_filter",
	.type = OBS_SOURCE_TYPE_FILTER,
	.output_flags = OBS_SOURCE_VIDEO,
	.get_name = dob_get_name,
	.create = dob_create,
	.destroy = dob_destroy,
	.update = dob_update,
	.video_tick = dob_tick,
	.video_render = dob_render,
	.get_properties = dob_properties,
	.get_defaults = dob_defaults,
};
