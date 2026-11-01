project(foveated-squish VERSION 0.2.0 LANGUAGES C)

add_obs_plugin(${PROJECT_NAME} SOURCES src/plugin-main.c src/squish-filter.c src/squish-math.c)

# Built-in copy of the shader, used when the data folder is missing.
target_embed_text(${PROJECT_NAME} data/foveated_squish.effect fsq_embedded_effect embedded-effect.h)
