/*
Content Warp
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
OBS_MODULE_USE_DEFAULT_LOCALE("content-warp", "en-US")

extern struct obs_source_info content_warp_filter;

const char *obs_module_name(void)
{
	return "Content Warp";
}

const char *obs_module_description(void)
{
	return "Warps the sides of the picture by detail so a 4:3 stream keeps a 1:1 centre";
}

bool obs_module_load(void)
{
	obs_register_source(&content_warp_filter);
	return true;
}
