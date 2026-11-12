# Edge restorer research (epic #20)

- `SPEC.md`: shapes, ops, weight file format. Start here.
- `restore_math.py`: numpy reference forward pass and the RSTR v1 loader/exporter.
- `restore_golden.json`: fixed-seed golden output. Checked by the test below.

Test (issue #21.5):

    python3 research/restore/restore_math.py --check

Prints `OK` and exits 0 when the reference matches the golden file.
