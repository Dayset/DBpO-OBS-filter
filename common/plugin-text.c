#include "plugin-text.h"
#include "text-table.h"

#include <stdio.h>

#define VIEW_WARNING "view_warning"

/* Built once in obs_module_load (single-threaded), read-only afterwards. */
static struct text_table *builtin_text;

void plugin_text_load(const char *embedded_en_us)
{
	text_table_free(builtin_text);
	builtin_text = text_table_parse(embedded_en_us);
}

void plugin_text_unload(void)
{
	text_table_free(builtin_text);
	builtin_text = NULL;
}

const char *plugin_text_find(const char *key)
{
	const char *out = NULL;
	if (obs_module_get_string(key, &out) && out)
		return out;
	return text_table_get(builtin_text, key);
}

const char *plugin_text(const char *key)
{
	const char *out = plugin_text_find(key);
	return out ? out : key;
}

static void decorate(obs_properties_t *props)
{
	char key[128];
	for (obs_property_t *p = obs_properties_first(props); p; obs_property_next(&p)) {
		const char *name = obs_property_name(p);
		const enum obs_property_type type = obs_property_get_type(p);
		if (name && *name) {
			snprintf(key, sizeof(key), "Hint.%s", name);
			const char *hint = plugin_text_find(key);
			if (hint)
				obs_property_set_long_description(p, hint);
			snprintf(key, sizeof(key), "Unit.%s", name);
			const char *unit = plugin_text_find(key);
			if (unit && type == OBS_PROPERTY_INT)
				obs_property_int_set_suffix(p, unit);
			else if (unit && type == OBS_PROPERTY_FLOAT)
				obs_property_float_set_suffix(p, unit);
		}
		if (type == OBS_PROPERTY_GROUP)
			decorate(obs_property_group_content(p));
	}
}

void plugin_ui_decorate(obs_properties_t *props)
{
	if (props)
		decorate(props);
}

void plugin_ui_show(obs_properties_t *props, const char *name, bool visible)
{
	obs_property_t *p = obs_properties_get(props, name);
	if (p)
		obs_property_set_visible(p, visible);
}

static bool view_modified(obs_properties_t *props, obs_property_t *p, obs_data_t *settings)
{
	const bool debug = obs_data_get_int(settings, obs_property_name(p)) != 0;
	obs_property_t *w = obs_properties_get(props, VIEW_WARNING);
	if (!w || obs_property_visible(w) == debug)
		return false;
	obs_property_set_visible(w, debug);
	return true;
}

void plugin_ui_add_view_warning(obs_properties_t *props, obs_property_t *view_list)
{
	obs_property_t *w = obs_properties_add_text(props, VIEW_WARNING, plugin_text("ViewWarning"), OBS_TEXT_INFO);
	obs_property_text_set_info_type(w, OBS_TEXT_INFO_WARNING);
	obs_property_set_visible(w, false);
	obs_property_set_modified_callback(view_list, view_modified);
}
