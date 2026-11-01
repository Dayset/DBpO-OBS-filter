/*
 * obs-headless: run an OBS filter plugin on raw frames without the OBS UI.
 *
 * Starts libobs with its OpenGL backend (works on a virtual X display with a
 * software GPU), loads a plugin module and puts the filter on a frame-feeding
 * source. OBS's own video loop provides the ticks (video_tick is not public);
 * a tick callback runs just before sources are ticked, so each time it runs
 * the filter has been ticked exactly once since the last call. The callback
 * renders the source through the filter, reads the result back, then uploads
 * the next input frame: strict lockstep, one output per input. Input and
 * output are raw RGBA frames on stdin/stdout, so ffmpeg can be on either side.
 *
 *   obs-headless <plugin.so> <plugin-data-dir> <filter-id> <width> <height>
 *                [key=value ...]   (settings: 12, 0.5, true/false)
 *
 * Exit code is non-zero if the filter could not be created or rendering failed;
 * all OBS log output goes to stderr.
 */

#include <obs.h>
#include <obs-module.h>
#include <obs-nix-platform.h>
#include <graphics/graphics.h>
#include <X11/Xlib.h>
#include <pthread.h>
#include <util/platform.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint32_t W, H;
static gs_texture_t *feed_tex;

/* ------------------------------------------------------------------ feed source */

static const char *feed_name(void *u)
{
	(void)u;
	return "feed";
}
static void *feed_create(obs_data_t *s, obs_source_t *src)
{
	(void)s;
	return src;
}
static void feed_destroy(void *d)
{
	(void)d;
}
static uint32_t feed_w(void *d)
{
	(void)d;
	return W;
}
static uint32_t feed_h(void *d)
{
	(void)d;
	return H;
}
static void feed_render(void *d, gs_effect_t *e)
{
	(void)d;
	(void)e;
	gs_effect_t *def = obs_get_base_effect(OBS_EFFECT_DEFAULT);
	gs_effect_set_texture(gs_effect_get_param_by_name(def, "image"), feed_tex);
	while (gs_effect_loop(def, "Draw"))
		gs_draw_sprite(feed_tex, 0, W, H);
}

static struct obs_source_info feed_info = {
	.id = "headless_feed",
	.type = OBS_SOURCE_TYPE_INPUT,
	/* Draws itself (runs its own effect loop), like capture sources. */
	.output_flags = OBS_SOURCE_VIDEO | OBS_SOURCE_CUSTOM_DRAW,
	.get_name = feed_name,
	.create = feed_create,
	.destroy = feed_destroy,
	.get_width = feed_w,
	.get_height = feed_h,
	.video_render = feed_render,
};

/* ------------------------------------------------------------------ log */

static void log_handler(int lvl, const char *fmt, va_list args, void *p)
{
	(void)p;
	if (lvl > LOG_INFO)
		return;
	vfprintf(stderr, fmt, args);
	fputc('\n', stderr);
}

static int render_errors;
static void count_errors(int lvl, const char *fmt, va_list args, void *p)
{
	char buf[1024];
	va_list copy;
	va_copy(copy, args);
	vsnprintf(buf, sizeof(buf), fmt, copy);
	va_end(copy);
	if (lvl <= LOG_WARNING || strstr(buf, "Not all shader parameters were set")) {
		render_errors++;
		fprintf(stderr, "[counted] ");
	}
	log_handler(lvl, fmt, args, p);
}

/* ------------------------------------------------------------------ frame pump */

static size_t frame_bytes;
static uint8_t *in_buf, *out_buf;
static obs_source_t *feed_src;
static gs_texrender_t *render_tr;
static gs_stagesurf_t *render_stage;
static bool pending; /* an uploaded frame awaits rendering */
static volatile bool finished;

static void render_pending(void)
{
	gs_texrender_reset(render_tr);
	if (gs_texrender_begin(render_tr, W, H)) {
		struct vec4 clear;
		vec4_zero(&clear);
		gs_clear(GS_CLEAR_COLOR, &clear, 0.0f, 0);
		gs_ortho(0.0f, (float)W, 0.0f, (float)H, -100.0f, 100.0f);
		gs_blend_state_push();
		gs_blend_function(GS_BLEND_ONE, GS_BLEND_ZERO);
		obs_source_video_render(feed_src);
		gs_blend_state_pop();
		gs_texrender_end(render_tr);
	}
	gs_stage_texture(render_stage, gs_texrender_get_texture(render_tr));
	uint8_t *data;
	uint32_t linesize;
	memset(out_buf, 0, frame_bytes);
	if (gs_stagesurface_map(render_stage, &data, &linesize)) {
		for (uint32_t y = 0; y < H; y++)
			memcpy(out_buf + (size_t)y * W * 4, data + (size_t)y * linesize, (size_t)W * 4);
		gs_stagesurface_unmap(render_stage);
	}
	fwrite(out_buf, 1, frame_bytes, stdout);
}

static long frames_done;

static void tick_cb(void *p, float seconds)
{
	(void)p;
	(void)seconds;
	if (finished)
		return;
	obs_enter_graphics();
	if (pending) {
		render_pending();
		frames_done++;
		pending = false;
	}
	if (fread(in_buf, 1, frame_bytes, stdin) == frame_bytes) {
		gs_texture_set_image(feed_tex, in_buf, W * 4, false);
		pending = true;
	} else {
		finished = true;
	}
	obs_leave_graphics();
}

int main(int argc, char **argv)
{
	if (argc < 6) {
		fprintf(stderr, "usage: %s plugin.so data-dir filter-id width height [key=value...]\n", argv[0]);
		return 2;
	}
	const char *module_path = argv[1], *data_dir = argv[2], *filter_id = argv[3];
	W = (uint32_t)atoi(argv[4]);
	H = (uint32_t)atoi(argv[5]);

	base_set_log_handler(count_errors, NULL);

	Display *dpy = XOpenDisplay(NULL);
	if (!dpy) {
		fprintf(stderr, "no X display (run under xvfb-run)\n");
		return 2;
	}
	obs_set_nix_platform(OBS_NIX_PLATFORM_X11_EGL);
	obs_set_nix_platform_display(dpy);

	if (!obs_startup("en-US", NULL, NULL)) {
		fprintf(stderr, "obs_startup failed\n");
		return 2;
	}
	struct obs_video_info ovi = {
		.graphics_module = "libobs-opengl",
		.fps_num = 60,
		.fps_den = 1,
		.base_width = W,
		.base_height = H,
		.output_width = W,
		.output_height = H,
		.output_format = VIDEO_FORMAT_RGBA,
		.adapter = 0,
		.gpu_conversion = true,
		.colorspace = VIDEO_CS_709,
		.range = VIDEO_RANGE_FULL,
		.scale_type = OBS_SCALE_BILINEAR,
	};
	int rv = obs_reset_video(&ovi);
	if (rv != OBS_VIDEO_SUCCESS) {
		fprintf(stderr, "obs_reset_video failed: %d\n", rv);
		return 2;
	}

	obs_register_source(&feed_info);
	obs_module_t *mod = NULL;
	if (obs_open_module(&mod, module_path, data_dir) != MODULE_SUCCESS || !obs_init_module(mod)) {
		fprintf(stderr, "failed to load %s\n", module_path);
		return 2;
	}

	obs_data_t *settings = obs_data_create();
	for (int i = 6; i < argc; i++) {
		char key[128];
		const char *eq = strchr(argv[i], '=');
		if (!eq || (size_t)(eq - argv[i]) >= sizeof(key))
			continue;
		memcpy(key, argv[i], (size_t)(eq - argv[i]));
		key[eq - argv[i]] = 0;
		const char *val = eq + 1;
		if (strcmp(val, "true") == 0 || strcmp(val, "false") == 0)
			obs_data_set_bool(settings, key, strcmp(val, "true") == 0);
		else if (strchr(val, '.'))
			obs_data_set_double(settings, key, atof(val));
		else
			obs_data_set_int(settings, key, atoll(val));
	}

	obs_source_t *src = obs_source_create_private("headless_feed", "feed", NULL);
	obs_source_t *flt = obs_source_create_private(filter_id, "filter", settings);
	obs_data_release(settings);
	if (!flt) {
		fprintf(stderr, "could not create filter %s\n", filter_id);
		return 3;
	}
	obs_source_filter_add(src, flt);

	obs_enter_graphics();
	feed_tex = gs_texture_create(W, H, GS_RGBA, 1, NULL, GS_DYNAMIC);
	obs_leave_graphics();

	frame_bytes = (size_t)W * H * 4;
	in_buf = malloc(frame_bytes);
	out_buf = malloc(frame_bytes);
	feed_src = src;
	obs_enter_graphics();
	render_tr = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
	render_stage = gs_stagesurface_create(W, H, GS_RGBA);
	obs_leave_graphics();
	render_errors = 0;
	obs_add_tick_callback(tick_cb, NULL);
	while (!finished)
		os_sleep_ms(5);
	obs_remove_tick_callback(tick_cb, NULL);
	fflush(stdout);
	fprintf(stderr, "obs-headless: %ld frames, %d render warnings/errors\n", frames_done, render_errors);

	obs_source_filter_remove(src, flt);
	obs_source_release(flt);
	obs_source_release(src);
	obs_enter_graphics();
	gs_stagesurface_destroy(render_stage);
	gs_texrender_destroy(render_tr);
	gs_texture_destroy(feed_tex);
	obs_leave_graphics();
	free(in_buf);
	free(out_buf);
	obs_shutdown();
	XCloseDisplay(dpy);
	return render_errors ? 4 : 0;
}
