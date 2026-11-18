#include <obs-module.h>
#include <util/platform.h>
#include <math.h>

/*
 * Frame-rate ladder for a 120 fps output. Band k is refreshed on every
 * BAND_DIV[k]-th output tick and holds its last frame in between.
 *   band 0 = centre, 120 fps (about 5% of the frame area)
 *   band 6 = far edges, 15 fps
 */
#define BANDS 7
static const uint32_t BAND_DIV[BANDS] = {1, 2, 3, 4, 5, 6, 8};

/* Gaussian: fps falls with distance d from the centre, G = exp(-d^2 / (2 sigma^2)).
 * d is measured so the frame edge midpoint is d = 1. The 120 fps ellipse (d < R0)
 * covers pi*R0^2/4 = 5% of the frame. Band index = floor((1 - G) * 7), so the
 * 120 fps band ends exactly at G = 6/7, which is d = R0. */
#define R0 0.2523f
#define SIGMA (R0 / sqrtf(2.0f * logf(7.0f / 6.0f)))

struct gh_info {
	obs_source_t *source;
	gs_texrender_t *fresh;
	gs_texrender_t *band[BANDS];
	gs_texrender_t *out;
	uint32_t cx;
	uint32_t cy;
	uint32_t tick;
	uint32_t whole_div; /* 1 = off, or 1,2,3,4,5,6,8 applied to the whole frame */
	bool gaussian;
	gs_effect_t *mask;
};

static const char *gh_effect_src =
	"uniform float4x4 ViewProj;\n"
	"uniform texture2d image;\n"
	"uniform float sigma;\n"
	"uniform int band;\n"
	"uniform int gaussian;\n"
	"sampler_state textureSampler {\n"
	"	Filter = Linear;\n"
	"	AddressU = Clamp;\n"
	"	AddressV = Clamp;\n"
	"};\n"
	"struct VertData {\n"
	"	float4 pos : POSITION;\n"
	"	float2 uv : TEXCOORD0;\n"
	"};\n"
	"VertData VSMask(VertData v_in)\n"
	"{\n"
	"	VertData v_out;\n"
	"	v_out.pos = mul(float4(v_in.pos.xyz, 1.0), ViewProj);\n"
	"	v_out.uv = v_in.uv;\n"
	"	return v_out;\n"
	"}\n"
	"float4 PSMask(VertData v_in) : TARGET\n"
	"{\n"
	"	float2 p = (v_in.uv - 0.5) * 2.0;\n"
	"	float g = exp(-dot(p, p) / (2.0 * sigma * sigma));\n"
	"	int idx = gaussian != 0 ? clamp((int)floor((1.0 - g) * 7.0), 0, 6) : 0;\n"
	"	float4 c = image.Sample(textureSampler, v_in.uv);\n"
	"	return float4(c.rgb, idx == band ? 1.0 : 0.0);\n"
	"}\n"
	"technique Draw\n"
	"{\n"
	"	pass\n"
	"	{\n"
	"		vertex_shader = VSMask(v_in);\n"
	"		pixel_shader = PSMask(v_in);\n"
	"	}\n"
	"}\n";

static const char *gh_get_name(void *type_data)
{
	UNUSED_PARAMETER(type_data);
	return "Gaussian Frame Hold";
}

/* Caller must be inside the graphics context. */
static void gh_free_textures(struct gh_info *f)
{
	gs_texrender_destroy(f->fresh);
	gs_texrender_destroy(f->out);
	for (int k = 0; k < BANDS; k++)
		gs_texrender_destroy(f->band[k]);
	f->fresh = f->out = NULL;
	for (int k = 0; k < BANDS; k++)
		f->band[k] = NULL;
}

static void gh_update(void *data, obs_data_t *settings)
{
	struct gh_info *f = data;
	f->whole_div = (uint32_t)obs_data_get_int(settings, "fps");
	f->gaussian = obs_data_get_bool(settings, "gaussian");
}

static void gh_defaults(obs_data_t *settings)
{
	obs_data_set_default_int(settings, "fps", 1);
	obs_data_set_default_bool(settings, "gaussian", false);
}

static void *gh_create(obs_data_t *settings, obs_source_t *source)
{
	struct gh_info *f = bzalloc(sizeof(*f));
	f->source = source;
	obs_enter_graphics();
	f->mask = gs_effect_create(gh_effect_src, "gauss_frame_hold_mask", NULL);
	obs_leave_graphics();
	gh_update(f, settings);
	return f;
}

static void gh_destroy(void *data)
{
	struct gh_info *f = data;
	obs_enter_graphics();
	gh_free_textures(f);
	gs_effect_destroy(f->mask);
	obs_leave_graphics();
	bfree(f);
}

static obs_properties_t *gh_properties(void *data)
{
	UNUSED_PARAMETER(data);
	obs_properties_t *ppts = obs_properties_create();
	obs_property_t *p = obs_properties_add_list(ppts, "fps", "Whole screen frame rate", OBS_COMBO_TYPE_LIST,
						    OBS_COMBO_FORMAT_INT);
	obs_property_list_add_int(p, "120 fps", 1);
	obs_property_list_add_int(p, "60 fps", 2);
	obs_property_list_add_int(p, "40 fps", 3);
	obs_property_list_add_int(p, "30 fps", 4);
	obs_property_list_add_int(p, "24 fps", 5);
	obs_property_list_add_int(p, "20 fps", 6);
	obs_property_list_add_int(p, "15 fps", 8);
	obs_properties_add_bool(ppts, "gaussian", "Gaussian: 120 fps centre (5%), falling to 15 fps at the edges");
	return ppts;
}

static void gh_tick(void *data, float seconds)
{
	UNUSED_PARAMETER(seconds);
	struct gh_info *f = data;
	f->tick++;
}

static uint32_t gh_band_div(struct gh_info *f, int k)
{
	return f->gaussian ? BAND_DIV[k] : f->whole_div;
}

static void gh_blit(gs_texrender_t *dst, gs_texture_t *src, uint32_t cx, uint32_t cy)
{
	gs_effect_t *def = obs_get_base_effect(OBS_EFFECT_DEFAULT);
	gs_eparam_t *image = gs_effect_get_param_by_name(def, "image");

	gs_texrender_reset(dst);
	gs_blend_state_push();
	gs_blend_function(GS_BLEND_ONE, GS_BLEND_ZERO);
	if (gs_texrender_begin(dst, cx, cy)) {
		struct vec4 clear_color;
		vec4_zero(&clear_color);
		gs_clear(GS_CLEAR_COLOR, &clear_color, 0.0f, 0);
		gs_ortho(0.0f, (float)cx, 0.0f, (float)cy, -100.0f, 100.0f);
		gs_effect_set_texture(image, src);
		while (gs_effect_loop(def, "Draw"))
			gs_draw_sprite(src, 0, cx, cy);
		gs_texrender_end(dst);
	}
	gs_blend_state_pop();
}

static void gh_video_render(void *data, gs_effect_t *effect)
{
	UNUSED_PARAMETER(effect);
	struct gh_info *f = data;
	obs_source_t *target = obs_filter_get_target(f->source);
	obs_source_t *parent = obs_filter_get_parent(f->source);
	if (!target || !parent) {
		obs_source_skip_video_filter(f->source);
		return;
	}

	uint32_t cx = obs_source_get_base_width(target);
	uint32_t cy = obs_source_get_base_height(target);
	if (!cx || !cy) {
		obs_source_skip_video_filter(f->source);
		return;
	}
	if (cx != f->cx || cy != f->cy || !f->fresh) {
		gh_free_textures(f);
		f->cx = cx;
		f->cy = cy;
		f->fresh = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
		f->out = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
		for (int k = 0; k < BANDS; k++)
			f->band[k] = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
		f->tick = 0;
	}

	/* Which bands are due this tick? */
	bool due[BANDS];
	bool any = false;
	for (int k = 0; k < BANDS; k++) {
		due[k] = (f->gaussian || k == 0) && (f->tick % gh_band_div(f, k)) == 0;
		any |= due[k];
	}

	/* Render the source once, then copy it into each due band. */
	if (any) {
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
		for (int k = 0; k < BANDS; k++)
			if (due[k])
				gh_blit(f->band[k], fresh_tex, cx, cy);
	}

	/* Composite: each band contributes only its own pixels. */
	gs_texrender_reset(f->out);
	if (gs_texrender_begin(f->out, cx, cy)) {
		struct vec4 clear_color;
		vec4_zero(&clear_color);
		gs_clear(GS_CLEAR_COLOR, &clear_color, 0.0f, 0);
		gs_ortho(0.0f, (float)cx, 0.0f, (float)cy, -100.0f, 100.0f);

		gs_eparam_t *image = gs_effect_get_param_by_name(f->mask, "image");
		gs_eparam_t *sigma = gs_effect_get_param_by_name(f->mask, "sigma");
		gs_eparam_t *band = gs_effect_get_param_by_name(f->mask, "band");
		gs_eparam_t *gauss = gs_effect_get_param_by_name(f->mask, "gaussian");
		gs_effect_set_float(sigma, SIGMA);
		gs_effect_set_int(gauss, f->gaussian ? 1 : 0);

		gs_blend_state_push();
		gs_blend_function(GS_BLEND_SRCALPHA, GS_BLEND_INVSRCALPHA);
		for (int k = 0; k < BANDS; k++) {
			gs_texture_t *tex = gs_texrender_get_texture(f->band[k]);
			if (!tex)
				continue;
			gs_effect_set_texture(image, tex);
			gs_effect_set_int(band, k);
			while (gs_effect_loop(f->mask, "Draw"))
				gs_draw_sprite(tex, 0, cx, cy);
		}
		gs_blend_state_pop();
		gs_texrender_end(f->out);
	}

	/* Output the composited frame. */
	gs_effect_t *def = obs_get_base_effect(OBS_EFFECT_DEFAULT);
	gs_texture_t *out_tex = gs_texrender_get_texture(f->out);
	gs_effect_set_texture(gs_effect_get_param_by_name(def, "image"), out_tex);
	gs_blend_state_push();
	gs_blend_function(GS_BLEND_ONE, GS_BLEND_ZERO);
	while (gs_effect_loop(def, "Draw"))
		gs_draw_sprite(out_tex, 0, cx, cy);
	gs_blend_state_pop();
}

struct obs_source_info gauss_frame_hold_filter = {
	.id = "gauss_frame_hold_filter",
	.type = OBS_SOURCE_TYPE_FILTER,
	.output_flags = OBS_OUTPUT_VIDEO | OBS_SOURCE_SRGB,
	.get_name = gh_get_name,
	.create = gh_create,
	.destroy = gh_destroy,
	.update = gh_update,
	.get_defaults = gh_defaults,
	.get_properties = gh_properties,
	.video_tick = gh_tick,
	.video_render = gh_video_render,
};

