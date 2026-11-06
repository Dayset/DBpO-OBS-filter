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
	double k;          /* Mbit/s per metric unit (sensitivity), default 1.35 */
	double alpha;      /* EMA weight, default 1/8 */
	int down_frames;   /* default 6 */
	int up_frames;     /* default 60 */
	int lowest;        /* lowest allowed code (1..3), default 3 */
	double table[4];   /* demand/budget upper bounds for codes 0..3: 1.5, 3, 6, inf */
};

struct stv_controller {
	struct stv_controller_cfg cfg;
	double hist[3];
	int hist_n;
	double ema;
	bool have_ema;
	int cur, want, run;
	double ratio;      /* last demand / budget, for the readout */
};

struct stv_controller_cfg stv_default_cfg(double budget_kbps);
void stv_controller_init(struct stv_controller *c, const struct stv_controller_cfg *cfg);
/* congestion 0..1 lowers the budget (budget * (1 - congestion), at most 0.9 off). */
int stv_controller_step(struct stv_controller *c, double metric, double congestion);

/* Viewer-side hold is in the extension; the sender side never needs it. */
