# Kill / resume test (issue #24.4)

Config `configs/l540_synth_float.json` with `--name killtest --steps 200 --until 200 --every 100`.

1. Uninterrupted run: 200 steps, loss log kept as the reference.
2. Killed with SIGKILL right after the step-100 checkpoint was written (log showed 100 lines, process still running).
3. Same command again: "resumed from step 100", ran to step 200.

`kill_resume_loss.csv` is the log after the resume (200 lines: steps 1-100 from run 1, 101-200 from run 2). The loss log is flushed at checkpoints only, so the lines written after step 100 before the kill were lost. No duplicate step numbers.

Compared with the uninterrupted run, every step matches (see the comment on #24).
