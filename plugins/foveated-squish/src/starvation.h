#pragma once

/* Starvation levels: pure math shared by the OBS filter (spec: research/foveation/STARVATION_SPEC.md,
 * reference: research/foveation/starvation_math.py). No libobs here so it can be unit tested. */

#include <stdbool.h>
#include <stdint.h>

#define STV_LEVELS 6     /* codes 0..5 are defined, the rest is reserved */
#define STV_MARKER_W 160
#define STV_MARKER_H 16
#define STV_CELL 16
#define STV_BITS 5

enum stv_kind { STV_PLAIN = 0, STV_SQUISH = 1 };

struct stv_rect {
	int w, h; /* picture size, origin is (0, 0) */
};

/* Level kind and size of the level `code` inside a pw x ph picture rect (spec 2). Unknown code -> level 0. */
enum stv_kind stv_kind(int code);
struct stv_rect stv_rect(int code, int pw, int ph);

/* Marker (spec 4). bits[5]: 4 code bits LSB first + even parity. cells[10]: 1 = white, 0 = black. */
void stv_marker_bits(int code, int bits[STV_BITS]);
void stv_marker_cells(int code, uint8_t cells[2 * STV_BITS]);
void stv_marker_pos(int frame_w, int frame_h, int *x, int *y);
/* Decode from the 10 inner-8x8 mean luma values (limited range, 16..235). Returns the code or -1. */
int stv_marker_decode(const double mean_luma[2 * STV_BITS]);

/* Detector (issue #12): causal level chooser. step() takes the temporal metric of the frame just completed
 * (mean |Y_t - Y_t-1| on the 240x135 luma image) and returns the code for the NEXT frame. */
struct stv_controller_cfg {
	double budget_kbps;
	double k;        /* Mbit/s per metric unit (sensitivity), default 1.35 */
	double alpha;    /* EMA weight, default 1/8 */
	int down_frames; /* default 6 */
	int up_frames;   /* default 60 */
	int lowest;      /* lowest allowed target (1..5), default 3 */
	double table[6]; /* demand/budget upper bounds for targets 0..5: 1.5, 3, 6, 12, 24, inf */
};

struct stv_controller {
	struct stv_controller_cfg cfg;
	double hist[3];
	int hist_n;
	double ema;
	bool have_ema;
	int cur, want, run;
	double ratio; /* last demand / budget, for the readout */
};

struct stv_controller_cfg stv_default_cfg(double budget_kbps);
void stv_controller_init(struct stv_controller *c, const struct stv_controller_cfg *cfg);
/* congestion 0..1 lowers the budget (budget * (1 - congestion), at most 0.9 off). */
int stv_controller_step(struct stv_controller *c, double metric, double congestion);

/* Marker v2 (spec 9): smooth steps. Row 1 = v1 marker with code STV_EXT_CODE, row 2 (16 px above) = 20 cells of
 * 8x16 carrying scale step a (0..63) and squeeze step q (0..7). */
#define STV_EXT_CODE 15
#define STV_A_MAX 63
#define STV_Q_MAX 7
#define STV_F_MIN (1.0 / 6.0)
#define STV_EXT_BITS 10
#define STV_EXT_CELL 8

double stv_frac(int a);           /* picture fraction 1 .. 1/6 */
double stv_squeeze(int a, int q); /* factor on the squish ratio r: frac(a) ^ (q / 7) */
int stv_a_for(double f);          /* nearest step for a fraction */
struct stv_rect stv_rect2(int a, int pw, int ph);
void stv_ext_pos(int frame_w, int frame_h, int *x, int *y);
void stv_ext_bits(int a, int q, int bits[STV_EXT_BITS]);
/* Bits packed for the shader: bit k = cell k is white. v1 row: 10 cells; extension row: 20 cells. */
unsigned stv_cells_v1(int code);
unsigned stv_cells_ext(int a, int q);

/* Linear ramp of (a, q) to a target over n frames; step() is called once per frame. */
struct stv_ramp {
	double a, q, a0, q0, ta, tq;
	int n, i;
};
void stv_ramp_init(struct stv_ramp *r);
void stv_ramp_go(struct stv_ramp *r, int a, int q, int frames);
void stv_ramp_step(struct stv_ramp *r, int *a, int *q);

/* Demo loop (spec 9.4): (a, q, ramp seconds, hold seconds). */
#define STV_DEMO_PHASES 5
extern const double stv_demo[STV_DEMO_PHASES][4];
struct stv_demo_state {
	int phase, frame;
};
/* Advances the demo by one frame at fps and drives the ramp. */
void stv_demo_step(struct stv_demo_state *d, struct stv_ramp *r, double fps);

/* Detector target index (0..5) -> picture fraction (1080, 720, 540, 360, 270, 180 at 1080p). */
double stv_target_frac(int idx);
