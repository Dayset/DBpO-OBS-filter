#!/bin/bash
# Turn a gameplay recording (AV1 1440p60, ~220 Mbit/s from the NVIDIA app) into the reference clips the bench uses.
# usage: prep_recording.sh recording.mp4 WORKDIR NAME [START_SECONDS] [SECONDS]
# Writes WORKDIR/NAME_1440.mkv (lossless FFV1 at 1440p, for E1/E4) and WORKDIR/NAME_1080.mkv (area-scaled 1080p60,
# what research/stack/stack_bench.py takes as REF). Needs an ffmpeg with an AV1 decoder (libdav1d).
set -euo pipefail
IN=$1; W=$2; N=$3; SS=${4:-0}; T=${5:-60}; mkdir -p "$W"
ffmpeg -y -ss "$SS" -t "$T" -i "$IN" -vf "fps=60,scale=2560:1440:flags=area,format=yuv444p" -c:v ffv1 -level 3 "$W/${N}_1440.mkv"
ffmpeg -y -i "$W/${N}_1440.mkv" -vf "scale=1920:1080:flags=area,format=yuv444p" -c:v ffv1 -level 3 "$W/${N}_1080.mkv"
ls -lh "$W/${N}_"*.mkv
