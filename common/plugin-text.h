#pragma once

/* Settings text, hints and units for one plugin.
 *
 * Every label comes from data/locale/en-US.ini. The same file is compiled
 * into the plugin (target_embed_locale in CMake), so the panel still reads
 * well if the data folder is missing, and there is one place to edit text.
 *
 * Besides labels, the locale file can hold, per setting name:
 *   Hint.<setting>="..."   tooltip: the (i) icon next to the label in OBS
 *   Unit.<setting>=" px"   suffix shown in the number box
 * plugin_ui_decorate() applies both to a whole panel, groups included. */

#include <obs-module.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Call from obs_module_load / obs_module_unload. */
void plugin_text_load(const char *embedded_en_us);
void plugin_text_unload(void);

/* Text for key: the loaded locale, else the built-in English, else NULL. */
const char *plugin_text_find(const char *key);

/* Like plugin_text_find, but returns the key itself when nothing has it. */
const char *plugin_text(const char *key);

/* Applies Hint.* and Unit.* to every property in props (and its groups).
 * Call at the end of get_properties. */
void plugin_ui_decorate(obs_properties_t *props);

/* Shows or hides one property (searched in groups too). */
void plugin_ui_show(obs_properties_t *props, const char *name, bool visible);

/* Adds an info line under view_list that warns while a debug or preview
 * view is selected (any value other than 0): OBS streams and records what
 * the filter outputs, so that view would go live. Uses the locale key
 * "ViewWarning". The list's modified callback is taken by this. */
void plugin_ui_add_view_warning(obs_properties_t *props, obs_property_t *view_list);

#ifdef __cplusplus
}
#endif
