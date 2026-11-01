project(perceptual-color-reduction VERSION 0.1.0 LANGUAGES C)

add_obs_plugin(${PROJECT_NAME} SOURCES src/plugin-main.c src/color-reduction-filter.c src/palette.c)

# Built-in copy of the shader, used when the data folder is missing.
target_embed_text(${PROJECT_NAME} data/perceptual_color_reduction.effect pcr_embedded_effect embedded-effect.h)
