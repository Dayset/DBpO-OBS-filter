/*
Content Warp: sides of the picture are warped by detail, centre stays 1:1.
Column detail is measured every N frames and cached.
*/

#include <obs-module.h>
#include <math.h>

#define BINS 240 /* detail bins across the source width */
#define ROWS 128 /* rows sampled per column for detail */

struct cw_info {
	obs_source_t *source;
	gs_texrender_t *fresh;
	gs_texrender_t *energy;
	gs_texrender_t *map;
	gs_effect_t *fx;
	uint32_t cx;
	uint32_t cy;
	uint32_t tick;
	uint32_t cache;
	float centre;
	bool mode_a;
};

static const char *cw_effect_src =
	"uniform float4x4 ViewProj;\n"
	"uniform texture2d image;\n"
	"uniform texture2d energy_tex;\n"
	"uniform texture2d warp;\n"
	"uniform float srcW;\n"
	"uniform float srcH;\n"
	"uniform float ew;\n"
	"uniform float p;\n"
	"uniform int mode_a;\n"
	"sampler_state s {\n"
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
	"float lum(float2 q)\n"
	"{\n"
	"	return dot(image.Sample(s, q).rgb, float3(0.3, 0.59, 0.11));\n"
	"}\n"
	"float4 PSEnergy(VertData v_in) : TARGET\n"
	"{\n"
	"	float e = 0.0;\n"
	"	[loop] for (int j = 0; j < 128; j++) {\n"
	"		float2 q = float2(v_in.uv.x, (j + 0.5) / 128.0);\n"
	"		e += abs(lum(q + float2(1.0 / srcW, 0.0)) - lum(q));\n"
	"		e += abs(lum(q + float2(0.0, 1.0 / srcH)) - lum(q));\n"
	"	}\n"
	"	return float4(e / 128.0, 0.0, 0.0, 1.0);\n"
	"}\n"
	"float e_at(int i)\n"
	"{\n"
	"	return energy_tex.Sample(s, float2((i + 0.5) / 240.0, 0.5)).r;\n"
	"}\n"
	"float src_u(float t, int i0, int n)\n"
	"{\n"
	"	float eps = 0.02;\n"
	"	float tot = 0.0;\n"
	"	[loop] for (int i = 0; i < 240; i++) {\n"
	"		if (i < i0) continue;\n"
	"		if (i >= i0 + n) break;\n"
	"		tot += e_at(i) + eps;\n"
	"	}\n"
	"	float target = t * tot;\n"
	"	float acc = 0.0;\n"
	"	[loop] for (int k = 0; k < 240; k++) {\n"
	"		if (k < i0) continue;\n"
	"		if (k >= i0 + n) break;\n"
	"		float w = e_at(k) + eps;\n"
	"		if (acc + w >= target)\n"
	"			return (k + (target - acc) / w) / 240.0;\n"
	"		acc += w;\n"
	"	}\n"
	"	return (i0 + n) / 240.0;\n"
	"}\n"
	"float4 PSMap(VertData v_in) : TARGET\n"
	"{\n"
	"	float xE = v_in.uv.x * ew;\n"
	"	float L = 0.5 - p * 0.5;\n"
	"	float R = 0.5 + p * 0.5;\n"
	"	float eL = (ew - p * srcW) * 0.5;\n"
	"	float u;\n"
	"	if (xE < eL) {\n"
	"		u = src_u(xE / eL, 0, (int)floor(L * 240.0));\n"
	"	} else if (xE >= ew - eL) {\n"
	"		int n0 = (int)ceil(R * 240.0);\n"
	"		u = src_u((xE - (ew - eL)) / eL, n0, 240 - n0);\n"
	"	} else {\n"
	"		u = L + (xE - eL) / srcW;\n"
	"	}\n"
	"	return float4(u, 0.0, 0.0, 1.0);\n"
	"}\n"
	"float4 PSRender(VertData v_in) : TARGET\n"
	"{\n"
	"	float ex = v_in.uv.x;\n"
	"	if (mode_a == 0) {\n"
	"		float local = v_in.uv.x * srcW - (srcW - ew) * 0.5;\n"
	"		if (local < 0.0 || local >= ew)\n"
	"			return float4(0.0, 0.0, 0.0, 1.0);\n"
	"		ex = local / ew;\n"
	"	}\n"
	"	float u = warp.Sample(s, float2(ex, 0.5)).r;\n"
	"	return image.Sample(s, float2(u, v_in.uv.y));\n"
	"}\n"
	"technique Energy\n"
	"{\n"
	"	pass\n"
	"	{\n"
	"		vertex_shader = VSMask(v_in);\n"
	"		pixel_shader = PSEnergy(v_in);\n"
	"	}\n"
	"}\n"
	"technique Map\n"
	"{\n"
	"	pass\n"
	"	{\n"
	"		vertex_shader = VSMask(v_in);\n"
	"		pixel_shader = PSMap(v_in);\n"
	"	}\n"
	"}\n"
	"technique Render\n"
	"{\n"
	"	pass\n"
	"	{\n"
	"		vertex_shader = VSMask(v_in);\n"
	"		pixel_shader = PSRender(v_in);\n"
	"	}\n"
	"}\n";

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
	gs_texrender_destroy(f->map);
	f->fresh = f->energy = f->map = NULL;
}

static void cw_update(void *data, obs_data_t *settings)
{
	struct cw_info *f = data;
	f->cache = (uint32_t)obs_data_get_int(settings, "cache");
	f->centre = (float)obs_data_get_int(settings, "centre") / 100.0f;
	f->mode_a = obs_data_get_int(settings, "mode") == 0;
}

static void cw_defaults(obs_data_t *settings)
{
	obs_data_set_default_int(settings, "centre", 20);
	obs_data_set_default_int(settings, "cache", 3);
	obs_data_set_default_int(settings, "mode", 0);
}

static void *cw_create(obs_data_t *settings, obs_source_t *source)
{
	struct cw_info *f = bzalloc(sizeof(*f));
	f->source = source;
	obs_enter_graphics();
	f->fx = gs_effect_create(cw_effect_src, "content_warp", NULL);
	obs_leave_graphics();
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

	obs_properties_add_int_slider(ppts, "centre", "Centre 1:1 width (%)", 5, 30, 1);
	obs_properties_add_int_slider(ppts, "cache", "Recompute warp every N frames", 2, 20, 1);
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
		cw_free_textures(f);
		f->cx = cx;
		f->cy = cy;
		f->fresh = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
		f->energy = gs_texrender_create(GS_RGBA32F, GS_ZS_NONE);
		f->map = gs_texrender_create(GS_RGBA32F, GS_ZS_NONE);
		f->tick = 0;
	}

	/* The 4:3 picture is cy * 4 / 3 wide in canvas pixels. */
	float ew = roundf((float)cy * 4.0f / 3.0f);
	float p = f->centre * cx;
	if (p > ew)
		p = ew;
	p /= (float)cx;

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
	gs_eparam_t *p_p = gs_effect_get_param_by_name(f->fx, "p");
	gs_eparam_t *mode_p = gs_effect_get_param_by_name(f->fx, "mode_a");

	gs_effect_set_float(srcW_p, (float)cx);
	gs_effect_set_float(srcH_p, (float)cy);
	gs_effect_set_float(ew_p, ew);
	gs_effect_set_float(p_p, p);
	gs_effect_set_int(mode_p, f->mode_a ? 1 : 0);

	/* Every N frames: detail per column, then the warp map from it. */
	if (f->tick % (f->cache ? f->cache : 1) == 0) {
		gs_texrender_reset(f->energy);
		if (gs_texrender_begin(f->energy, BINS, 1)) {
			struct vec4 clear_color;
			vec4_zero(&clear_color);
			gs_clear(GS_CLEAR_COLOR, &clear_color, 0.0f, 0);
			gs_ortho(0.0f, (float)BINS, 0.0f, 1.0f, -100.0f, 100.0f);
			gs_effect_set_texture(image, fresh_tex);
			while (gs_effect_loop(f->fx, "Energy"))
				gs_draw_sprite(NULL, 0, BINS, 1);
			gs_texrender_end(f->energy);
		}

		gs_texrender_reset(f->map);
		if (gs_texrender_begin(f->map, (uint32_t)ew, 1)) {
			struct vec4 clear_color;
			vec4_zero(&clear_color);
			gs_clear(GS_CLEAR_COLOR, &clear_color, 0.0f, 0);
			gs_ortho(0.0f, ew, 0.0f, 1.0f, -100.0f, 100.0f);
			gs_effect_set_texture(energy_p, gs_texrender_get_texture(f->energy));
			gs_effect_set_texture(image, fresh_tex);
			while (gs_effect_loop(f->fx, "Map"))
				gs_draw_sprite(NULL, 0, (uint32_t)ew, 1);
			gs_texrender_end(f->map);
		}
	}

	/* Every frame: sample the source through the cached map. */
	gs_effect_set_texture(image, fresh_tex);
	gs_effect_set_texture(energy_p, gs_texrender_get_texture(f->energy));
	gs_effect_set_texture(warp_p, gs_texrender_get_texture(f->map));
	gs_blend_state_push();
	gs_blend_function(GS_BLEND_ONE, GS_BLEND_ZERO);
	while (gs_effect_loop(f->fx, "Render"))
		gs_draw_sprite(NULL, 0, cx, cy);
	gs_blend_state_pop();
}

struct obs_source_info content_warp_filter = {
	.id = "content_warp_filter",
	.type = OBS_SOURCE_TYPE_FILTER,
	.output_flags = OBS_OUTPUT_VIDEO | OBS_SOURCE_SRGB,
	.get_name = cw_get_name,
	.create = cw_create,
	.destroy = cw_destroy,
	.update = cw_update,
	.get_defaults = cw_defaults,
	.get_properties = cw_properties,
	.video_tick = cw_tick,
	.video_render = cw_video_render,
};
