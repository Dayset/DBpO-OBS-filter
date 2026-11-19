# Gaussian Frame Hold

Holds frames per region so a 120 fps output can spend its frames where the viewer looks.

- **Whole screen frame rate:** 120, 60, 40, 30, 24, 20 or 15 fps for the whole picture.
- **Gaussian:** the centre (about 5% of the frame area) stays at 120 fps. The rate falls through 60, 40, 30, 24 and 20 fps and reaches 15 fps at the far edges. The 15 fps band covers about 37% of the frame.

Every rate divides 120 evenly, so each step holds frames for a whole number of output ticks. 45 fps isn't offered because it can't be made by whole holds.

The output must be set to 120 fps for the rates to be correct.

Status: built in CI, not yet tested in OBS.
