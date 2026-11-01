project(directional-object-blur VERSION 0.6.0 LANGUAGES C)

add_obs_plugin(
  ${PROJECT_NAME}
  SOURCES src/plugin-main.c src/directional-blur-filter.c src/camera-model.c src/mouse-sync.c src/raw-mouse.c
)

# Built-in copy of the shader, used when the data folder is missing.
target_embed_text(${PROJECT_NAME} data/directional_object_blur.effect dob_embedded_effect embedded-effect.h)
