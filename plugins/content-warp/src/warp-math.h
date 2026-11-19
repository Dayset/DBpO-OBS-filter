/*
Content Warp: CPU warp map. Pure C, no OBS dependencies, so it can be unit tested.
*/
#ifndef CW_WARP_MATH_H
#define CW_WARP_MATH_H

/*
Build the warp map for one frame.

energy: `bins` detail values across the source width (>= 0).
p:      centre width as a fraction of the source width (0..1).
ew:     output picture width in canvas pixels (4:3 picture width).
srcW:   source width in canvas pixels.
map:    receives map_w values; map[x] is the source position in [0, 1]
        that output pixel x samples.
*/
void cw_build_map(const float *energy, int bins, float p, float ew, float srcW, float *map, int map_w);

#endif
