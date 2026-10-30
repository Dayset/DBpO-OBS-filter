#pragma once

#include <stdbool.h>
#include <stddef.h>

/* Camera motion as an affine field over normalized coordinates p = (u - 0.5, v - 0.5):
 *   motion.x = x[0] + x[1] * p.x + x[2] * p.y
 *   motion.y = y[0] + y[1] * p.x + y[2] * p.y
 * in full-resolution pixels per frame. Covers pan, zoom (moving forward/back),
 * roll and shear. */
struct camera_model {
	float x[3];
	float y[3];
};

/* Robustly fits the model to a w*h grid of RGBA float samples
 * (R, G = motion in px, B = confidence 0..1, A unused). Rows are `stride`
 * floats apart. Moving objects and HUD elements are rejected as outliers
 * (iteratively reweighted least squares with a Tukey loss).
 * Returns false and zeroes the model when too few samples are reliable. */
bool camera_model_fit(const float *rgba, size_t w, size_t h, size_t stride, float min_confidence,
		      struct camera_model *out);

/* out = lerp(fresh, previous, smoothing), smoothing in [0, 1). */
void camera_model_smooth(struct camera_model *previous, const struct camera_model *fresh, float smoothing);
