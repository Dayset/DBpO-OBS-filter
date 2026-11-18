/*
Gaussian Frame Hold
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

OBS_DECLARE_MODULE()
OBS_MODULE_AUTHOR("dayset")

extern struct obs_source_info gauss_frame_hold_filter;

const char *obs_module_name(void)
{
	return "Gaussian Frame Hold";
}

const char *obs_module_description(void)
{
	return "Holds frames per region: whole-screen rate or a Gaussian falloff from a 120 fps centre to 15 fps edges";
}

bool obs_module_load(void)
{
	obs_register_source(&gauss_frame_hold_filter);
	return true;
}
