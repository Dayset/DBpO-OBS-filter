/*
Content Warp: sides of the picture are warped by detail, centre stays 1:1.
Column detail is measured on the GPU every N frames, read back, and the
warp map is built on the CPU (warp-math.c). The map is uploaded as a 1-row texture.
*/

#include <obs-module.h>
#include <util/bmem.h>
#include <math.h>
#include <string.h>
#include "warp-math.h"
#include "embedded-effect.h"

#define BINS 240 /* detail bins across the source width */
#define BLEND_PERIODS 4.0f /* the shown warp glides to each new measurement over this many recompute periods */

struct cw_info {
	obs_source_t *source;
	gs_effect_t *fx;
	gs_texrender_t *fresh;
	gs_texrender_t *energy;
	gs_stagesurf_t *stage[2];
	bool stage_full[2];
	int stage_i;
	gs_texture_t *warp;
	float *shown;  /* warp map drawn this frame; glides towards target */
	float *target; /* warp map from the latest detail measurement */
	int map_w;
	float bins[BINS];
	float p;
	float ew;
	uint32_t cx;
	uint32_t cy;
	uint32_t tick;
	uint32_t cache;
	float centre;
	bool mode_a;
	int view;
};

static const char *cw_get_name(void *type_data)
{
	UNUSED_PARAMETER(type_data);
	return "Content Warp";
}

/* Caller must be inside the graphics context. */
static void cw_free_textures(struct cw_info *f)
{
	gs_texrender_destroy(f->fresh);
	gs_texrender_destroy(f->energy);
	gs_stagesurface_destroy(f->stage[0]);
	gs_stagesurface_destroy(f->stage[1]);
	gs_texture_destroy(f->warp);
	bfree(f->shown);
	bfree(f->target);
	f->fresh = f->energy = NULL;
	f->stage[0] = f->stage[1] = NULL;
	f->warp = NULL;
	f->shown = f->target = NULL;
	f->stage_full[0] = f->stage_full[1] = false;
}

/* Called after each detail measurement. */
static void cw_build_target(struct cw_info *f)
{
	cw_build_map(f->bins, BINS, f->p, f->ew, (float)f->cx, f->target, f->map_w);
}

/* Called every frame: move the shown map a step towards the target and upload it. */
static void cw_blend_map(struct cw_info *f)
{
	float k = 1.0f / (BLEND_PERIODS * (float)(f->cache ? f->cache : 1));
	for (int x = 0; x < f->map_w; x++)
		f->shown[x] += (f->target[x] - f->shown[x]) * k;
	gs_texture_set_image(f->warp, (const uint8_t *)f->shown, (uint32_t)(sizeof(float) * (size_t)f->map_w), false);
}

static void cw_update(void *data, obs_data_t *settings)
{
	struct cw_info *f = data;
	f->cache = (uint32_t)obs_data_get_int(settings, "cache");
	f->centre = (float)obs_data_get_int(settings, "centre") / 100.0f;
	f->mode_a = obs_data_get_int(settings, "mode") == 0;
	f->view = (int)obs_data_get_int(settings, "view");
}

static void cw_defaults(obs_data_t *settings)
{
	obs_data_set_default_int(settings, "centre", 20);
	obs_data_set_default_int(settings, "cache", 3);
	obs_data_set_default_int(settings, "mode", 0);
	obs_data_set_default_int(settings, "view", 0);
}

static void *cw_create(obs_data_t *settings, obs_source_t *source)
{
	struct cw_info *f = bzalloc(sizeof(*f));
	f->source = source;

	char *path = obs_module_file("content_warp.effect");
	char *errors = NULL;
	obs_enter_graphics();
	if (path) {
		f->fx = gs_effect_create_from_file(path, &errors);
		if (!f->fx)
			blog(LOG_WARNING, "[content-warp] '%s' failed to compile, using built-in copy: %s", path,
			     errors ? errors : "(no details)");
		bfree(errors);
		errors = NULL;
	}
	if (!f->fx) {
		f->fx = gs_effect_create(cw_embedded_effect, "content_warp.effect", &errors);
		if (!f->fx)
			blog(LOG_ERROR, "[content-warp] built-in shader failed to compile: %s",
			     errors ? errors : "(no details)");
		bfree(errors);
	}
	obs_leave_graphics();
	bfree(path);

	cw_update(f, settings);
	return f;
}

static void cw_destroy(void *data)
{
	struct cw_info *f = data;
	obs_enter_graphics();
	cw_free_textures(f);
	gs_effect_destroy(f->fx);
	obs_leave_graphics();
	bfree(f);
}

static obs_properties_t *cw_properties(void *data)
{
	UNUSED_PARAMETER(data);
	obs_properties_t *ppts = obs_properties_create();

	obs_property_t *p = obs_properties_add_list(ppts, "mode", "Output", OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_INT);
	obs_property_list_add_int(p, "A: 4:3 canvas (set the canvas to 4:3 and stretch the source)", 0);
	obs_property_list_add_int(p, "B: 16:9 canvas, 4:3 picture with black bars", 1);
	obs_property_set_long_description(p, "A: set the canvas to 4:3. B: keep 16:9 and add black bars.");

	obs_property_t *c = obs_properties_add_int_slider(ppts, "centre", "Centre 1:1 width (%)", 5, 60, 1);
	obs_property_set_long_description(c, "Width of the untouched 1:1 band in the middle, as a percent of the source.");

	obs_property_t *n = obs_properties_add_int_slider(ppts, "cache", "Recompute warp every N frames", 2, 20, 1);
	obs_property_set_long_description(n, "How often the detail is measured. Higher is cheaper but reacts more slowly.");

	obs_property_t *v = obs_properties_add_list(ppts, "view", "Debug view", OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_INT);
	obs_property_list_add_int(v, "Final", 0);
	obs_property_list_add_int(v, "Source (no warp)", 1);
	obs_property_list_add_int(v, "Energy (detail per column)", 2);
	obs_property_list_add_int(v, "Warp map", 3);
	obs_property_set_long_description(v, "Shows which stage is working. Use Final for the real output.");
	return ppts;
}

static void cw_tick(void *data, float seconds)
{
	UNUSED_PARAMETER(seconds);
	struct cw_info *f = data;
	f->tick++;
}

static void cw_video_render(void *data, gs_effect_t *effect)
{
	UNUSED_PARAMETER(effect);
	struct cw_info *f = data;
	obs_source_t *target = obs_filter_get_target(f->source);
	obs_source_t *parent = obs_filter_get_parent(f->source);
	if (!target || !parent || !f->fx) {
		obs_source_skip_video_filter(f->source);
		return;
	}

	uint32_t cx = obs_source_get_base_width(target);
	uint32_t cy = obs_source_get_base_height(target);
	if (!cx || !cy) {
		obs_source_skip_video_filter(f->source);
		return;
	}

	/* The 4:3 picture is cy * 4 / 3 wide in canvas pixels. */
	float ew = roundf((float)cy * 4.0f / 3.0f);
	int map_w = (int)ew;

	if (cx != f->cx || cy != f->cy || !f->fresh) {
		cw_free_textures(f);
		f->cx = cx;
		f->cy = cy;
		f->map_w = map_w;
		f->fresh = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
		f->energy = gs_texrender_create(GS_RGBA32F, GS_ZS_NONE);
		f->stage[0] = gs_stagesurface_create(BINS, 1, GS_RGBA32F);
		f->stage[1] = gs_stagesurface_create(BINS, 1, GS_RGBA32F);
		f->warp = gs_texture_create(map_w, 1, GS_R32F, 1, NULL, GS_DYNAMIC);
		f->tick = 0;
		f->stage_i = 0;
		memset(f->bins, 0, sizeof(f->bins));
		f->p = 0.0f;
		f->ew = ew;
		f->shown = bzalloc(sizeof(float) * (size_t)map_w);
		f->target = bzalloc(sizeof(float) * (size_t)map_w);
		if (f->warp) {
			/* no detail yet: even spread, so the picture is never blank */
			cw_build_target(f);
			memcpy(f->shown, f->target, sizeof(float) * (size_t)map_w);
			gs_texture_set_image(f->warp, (const uint8_t *)f->shown, (uint32_t)(sizeof(float) * (size_t)map_w), false);
		}
	}

	f->ew = ew;
	f->p = f->centre;
	if (f->p * (float)cx > ew)
		f->p = ew / (float)cx;

	/* Render the source once per frame. */
	gs_texrender_reset(f->fresh);
	gs_blend_state_push();
	gs_blend_function(GS_BLEND_ONE, GS_BLEND_ZERO);
	if (gs_texrender_begin(f->fresh, cx, cy)) {
		struct vec4 clear_color;
		vec4_zero(&clear_color);
		gs_clear(GS_CLEAR_COLOR, &clear_color, 0.0f, 0);
		gs_ortho(0.0f, (float)cx, 0.0f, (float)cy, -100.0f, 100.0f);
		obs_source_video_render(target);
		gs_texrender_end(f->fresh);
	}
	gs_blend_state_pop();
	gs_texture_t *fresh_tex = gs_texrender_get_texture(f->fresh);

	gs_eparam_t *image = gs_effect_get_param_by_name(f->fx, "image");
	gs_eparam_t *energy_p = gs_effect_get_param_by_name(f->fx, "energy_tex");
	gs_eparam_t *warp_p = gs_effect_get_param_by_name(f->fx, "warp");
	gs_eparam_t *srcW_p = gs_effect_get_param_by_name(f->fx, "srcW");
	gs_eparam_t *srcH_p = gs_effect_get_param_by_name(f->fx, "srcH");
	gs_eparam_t *ew_p = gs_effect_get_param_by_name(f->fx, "ew");
	gs_eparam_t *mode_p = gs_effect_get_param_by_name(f->fx, "mode_a");
	gs_eparam_t *view_p = gs_effect_get_param_by_name(f->fx, "view");

	gs_effect_set_float(srcW_p, (float)cx);
	gs_effect_set_float(srcH_p, (float)cy);
	gs_effect_set_float(ew_p, ew);
	gs_effect_set_int(mode_p, f->mode_a ? 1 : 0);
	gs_effect_set_int(view_p, f->view);

	/* Every N frames: measure detail on the GPU and stage it for readback. */
	if (f->tick % (f->cache ? f->cache : 1) == 0) {
		gs_texrender_reset(f->energy);
		if (gs_texrender_begin(f->energy, BINS, 1)) {
			struct vec4 clear_color;
			vec4_zero(&clear_color);
			gs_clear(GS_CLEAR_COLOR, &clear_color, 0.0f, 0);
			gs_ortho(0.0f, (float)BINS, 0.0f, 1.0f, -100.0f, 100.0f);
			gs_effect_set_texture(image, fresh_tex);
			gs_effect_set_float(srcW_p, (float)cx);
			gs_effect_set_float(srcH_p, (float)cy);
			while (gs_effect_loop(f->fx, "Energy"))
				gs_draw_sprite(NULL, 0, BINS, 1);
			gs_texrender_end(f->energy);
		}

		gs_texture_t *energy_tex = gs_texrender_get_texture(f->energy);
		if (energy_tex) {
			int w = f->stage_i;
			gs_stage_texture(f->stage[w], energy_tex);
			f->stage_full[w] = true;

			/* Read the slot staged one period ago, then write the other slot next time. */
			int r = w ^ 1;
			f->stage_i = r;
			if (f->stage_full[r]) {
				uint8_t *data;
				uint32_t linesize;
				if (gs_stagesurface_map(f->stage[r], &data, &linesize)) {
					const float *row = (const float *)data;
					for (int i = 0; i < BINS; i++)
						f->bins[i] = row[i * 4];
					gs_stagesurface_unmap(f->stage[r]);
					cw_build_target(f);
				}
				f->stage_full[r] = false;
			}
		}
	}

	/* Every frame: glide the shown map towards the latest target, then draw. */
	if (f->warp)
		cw_blend_map(f);
	gs_blend_state_push();
	gs_blend_function(GS_BLEND_ONE, GS_BLEND_ZERO);
	if (f->view == 0) {
		gs_effect_set_texture(image, fresh_tex);
		gs_effect_set_texture(warp_p, f->warp);
		while (gs_effect_loop(f->fx, "Render"))
			gs_draw_sprite(NULL, 0, cx, cy);
	} else {
		gs_effect_set_texture(image, fresh_tex);
		gs_effect_set_texture(energy_p, gs_texrender_get_texture(f->energy));
		gs_effect_set_texture(warp_p, f->warp);
		while (gs_effect_loop(f->fx, "Show"))
			gs_draw_sprite(NULL, 0, cx, cy);
	}
	gs_blend_state_pop();
}

struct obs_source_info content_warp_filter = {
	.id = "content_warp_filter",
	.type = OBS_SOURCE_TYPE_FILTER,
	.output_flags = OBS_OUTPUT_VIDEO,
	.get_name = cw_get_name,
	.create = cw_create,
	.destroy = cw_destroy,
	.update = cw_update,
	.get_defaults = cw_defaults,
	.get_properties = cw_properties,
	.video_tick = cw_tick,
	.video_render = cw_video_render,
};
