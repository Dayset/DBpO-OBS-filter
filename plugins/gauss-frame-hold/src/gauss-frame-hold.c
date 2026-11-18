/*
Gaussian Frame Hold: holds frames per region of the picture.
Each region is refreshed only on the output ticks its frame rate allows.
The output must be 120 fps for the rates to be exact.
*/

#include <obs-module.h>
#include <math.h>

#define BANDS 7
#define GRID 256

/* Band k is refreshed on every BAND_DIV[k]-th output tick (120 / fps). */
static const uint32_t BAND_DIV[BANDS] = {1, 2, 3, 4, 5, 6, 8};
static const char *PCT_KEYS[BANDS] = {"pct_120", "pct_60", "pct_40", "pct_30", "pct_24", "pct_20", "pct_15"};
static const int DEFAULT_PCT[BANDS] = {5, 6, 7, 9, 13, 23, 37};

struct gh_info {
	obs_source_t *source;
	gs_texrender_t *fresh;
	gs_texrender_t *band[BANDS];
	gs_texrender_t *out;
	uint32_t cx;
	uint32_t cy;
	uint32_t tick;
	uint32_t whole_div;
	bool region;
	bool rect;
	float feather;
	float radius[BANDS - 1];
	gs_effect_t *mask;
};

static const char *gh_effect_src =
	"uniform float4x4 ViewProj;\n"
	"uniform texture2d image;\n"
	"uniform int band;\n"
	"uniform int rect;\n"
	"uniform float feather;\n"
	"uniform float r0;\n"
	"uniform float r1;\n"
	"uniform float r2;\n"
	"uniform float r3;\n"
	"uniform float r4;\n"
	"uniform float r5;\n"
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
	"float ramp(float d, float r, float w)\n"
	"{\n"
	"	return clamp(0.5 - (d - r) / max(w, 0.00001), 0.0, 1.0);\n"
	"}\n"
	"float4 PSMask(VertData v_in) : TARGET\n"
	"{\n"
	"	float2 p = (v_in.uv - 0.5) * 2.0;\n"
	"	float d = rect != 0 ? max(abs(p.x), abs(p.y)) : length(p);\n"
	"	float f = feather * 0.5;\n"
	"	float g0 = ramp(d, r0, f * r0);\n"
	"	float g1 = ramp(d, r1, f * (r1 - r0));\n"
	"	float g2 = ramp(d, r2, f * (r2 - r1));\n"
	"	float g3 = ramp(d, r3, f * (r3 - r2));\n"
	"	float g4 = ramp(d, r4, f * (r4 - r3));\n"
	"	float g5 = ramp(d, r5, f * (r5 - r4));\n"
	"	float w0 = g0;\n"
	"	float w1 = max(g1 - g0, 0.0);\n"
	"	float w2 = max(g2 - g1, 0.0);\n"
	"	float w3 = max(g3 - g2, 0.0);\n"
	"	float w4 = max(g4 - g3, 0.0);\n"
	"	float w5 = max(g5 - g4, 0.0);\n"
	"	float w6 = max(1.0 - g5, 0.0);\n"
	"	float total = w0 + w1 + w2 + w3 + w4 + w5 + w6;\n"
	"	float wb = band == 0 ? w0 : band == 1 ? w1 : band == 2 ? w2 : band == 3 ? w3\n"
	"		 : band == 4 ? w4 : band == 5 ? w5 : w6;\n"
	"	wb = total > 0.0 ? wb / total : 0.0;\n"
	"	float4 c = image.Sample(textureSampler, v_in.uv);\n"
	"	return float4(c.rgb * wb, wb);\n"
	"}\n"
	"technique Draw\n"
	"{\n"
	"	pass\n"
	"	{\n"
	"		vertex_shader = VSMask(v_in);\n"
	"		pixel_shader = PSMask(v_in);\n"
	"	}\n"
	"}\n";

/* Share of the frame inside distance r. The frame spans [-1, 1] on both axes. */
static float gh_area_within(bool rect, float r)
{
	int inside = 0;
	for (int y = 0; y < GRID; y++) {
		float py = ((y + 0.5f) / GRID) * 2.0f - 1.0f;
		for (int x = 0; x < GRID; x++) {
			float px = ((x + 0.5f) / GRID) * 2.0f - 1.0f;
			float d = rect ? fmaxf(fabsf(px), fabsf(py)) : sqrtf(px * px + py * py);
			inside += d <= r;
		}
	}
	return (float)inside / (GRID * GRID);
}

/* Boundary k sits where the cumulative share of bands 0..k is reached. */
static void gh_compute_radii(struct gh_info *f, const int pct[BANDS])
{
	int total = 0;
	for (int k = 0; k < BANDS; k++)
		total += pct[k];
	if (total == 0) {
		/* No shares set: everything stays in band 0 (120 fps). */
		for (int k = 0; k < BANDS - 1; k++)
			f->radius[k] = 2.0f;
		return;
	}

	float cum = 0.0f;
	for (int k = 0; k < BANDS - 1; k++) {
		cum += (float)pct[k] / total;
		float lo = 0.0f;
		float hi = 1.5f;
		for (int i = 0; i < 30; i++) {
			float mid = 0.5f * (lo + hi);
			if (gh_area_within(f->rect, mid) < cum)
				lo = mid;
			else
				hi = mid;
		}
		f->radius[k] = hi;
	}
}

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
	f->region = obs_data_get_bool(settings, "region");
	f->rect = obs_data_get_bool(settings, "rect");
	f->feather = (float)obs_data_get_int(settings, "feather") / 100.0f;

	int pct[BANDS];
	for (int k = 0; k < BANDS; k++)
		pct[k] = (int)obs_data_get_int(settings, PCT_KEYS[k]);
	gh_compute_radii(f, pct);
}

static void gh_defaults(obs_data_t *settings)
{
	obs_data_set_default_int(settings, "fps", 1);
	obs_data_set_default_bool(settings, "region", true);
	obs_data_set_default_bool(settings, "rect", false);
	obs_data_set_default_int(settings, "feather", 50);
	for (int k = 0; k < BANDS; k++)
		obs_data_set_default_int(settings, PCT_KEYS[k], DEFAULT_PCT[k]);
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

	obs_properties_add_bool(ppts, "region", "Per-region rates (uses the shares below)");
	obs_properties_add_bool(ppts, "rect", "Rectangle shape (off = oval)");
	obs_properties_add_int_slider(ppts, "feather", "Smooth edges between rates (%)", 0, 100, 1);

	static const char *labels[BANDS] = {"120 fps share of screen (%)", "60 fps share of screen (%)",
					    "40 fps share of screen (%)",  "30 fps share of screen (%)",
					    "24 fps share of screen (%)",  "20 fps share of screen (%)",
					    "15 fps share of screen (%)"};
	for (int k = 0; k < BANDS; k++)
		obs_properties_add_int_slider(ppts, PCT_KEYS[k], labels[k], 0, 100, 1);
	return ppts;
}

static void gh_tick(void *data, float seconds)
{
	UNUSED_PARAMETER(seconds);
	struct gh_info *f = data;
	f->tick++;
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
		obs_enter_graphics();
		gh_free_textures(f);
		obs_leave_graphics();
		f->cx = cx;
		f->cy = cy;
		f->fresh = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
		f->out = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
		for (int k = 0; k < BANDS; k++)
			f->band[k] = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
		f->tick = 0;
	}

	/* Whole-screen mode uses band 0 only, at the chosen rate. */
	bool due[BANDS];
	bool any = false;
	for (int k = 0; k < BANDS; k++) {
		uint32_t div = f->region ? BAND_DIV[k] : f->whole_div;
		due[k] = (f->region || k == 0) && (f->tick % div) == 0;
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

	/* Composite: each band is weighted per pixel and the weights add to 1. */
	gs_texrender_reset(f->out);
	if (gs_texrender_begin(f->out, cx, cy)) {
		struct vec4 clear_color;
		vec4_zero(&clear_color);
		gs_clear(GS_CLEAR_COLOR, &clear_color, 0.0f, 0);
		gs_ortho(0.0f, (float)cx, 0.0f, (float)cy, -100.0f, 100.0f);

		gs_eparam_t *image = gs_effect_get_param_by_name(f->mask, "image");
		gs_eparam_t *band_p = gs_effect_get_param_by_name(f->mask, "band");
		gs_eparam_t *rect_p = gs_effect_get_param_by_name(f->mask, "rect");
		gs_eparam_t *feather_p = gs_effect_get_param_by_name(f->mask, "feather");
		gs_eparam_t *r_p[BANDS - 1];
		static const char *r_names[BANDS - 1] = {"r0", "r1", "r2", "r3", "r4", "r5"};
		for (int k = 0; k < BANDS - 1; k++) {
			r_p[k] = gs_effect_get_param_by_name(f->mask, r_names[k]);
			gs_effect_set_float(r_p[k], f->region ? f->radius[k] : 2.0f);
		}
		gs_effect_set_int(rect_p, f->rect ? 1 : 0);
		gs_effect_set_float(feather_p, f->region ? f->feather : 0.0f);

		gs_blend_state_push();
		gs_blend_function(GS_BLEND_ONE, GS_BLEND_ONE);
		for (int k = 0; k < BANDS; k++) {
			if (!f->region && k > 0)
				break;
			gs_texture_t *tex = gs_texrender_get_texture(f->band[k]);
			if (!tex)
				continue;
			gs_effect_set_texture(image, tex);
			gs_effect_set_int(band_p, k);
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
