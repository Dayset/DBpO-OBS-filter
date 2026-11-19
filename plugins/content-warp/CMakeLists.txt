project(content-warp VERSION 0.1.0 LANGUAGES C)

add_obs_plugin(${PROJECT_NAME} SOURCES src/plugin-main.c src/content-warp.c src/warp-math.c)

# Built-in copy of the shader, used when the data folder is missing.
target_embed_text(${PROJECT_NAME} data/content_warp.effect cw_embedded_effect embedded-effect.h)
