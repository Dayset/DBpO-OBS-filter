project(gauss-frame-hold VERSION 0.1.0 LANGUAGES C)

add_obs_plugin(${PROJECT_NAME} SOURCES src/plugin-main.c src/gauss-frame-hold.c)
