#!/bin/bash
# Runs every arm of research/stack (resumable: finished arms are skipped). Usage: run_all.sh WORK FOOTAGE_DIR
# FOOTAGE_DIR holds the release clips as v1.mp4 .. v4.mp4. $VMAF_FFMPEG = an ffmpeg build with libvmaf.
set -e
S=$(cd "$(dirname "$0")" && pwd); W=$1; F=$2; B="python3 $S/stack_bench.py"; mkdir -p $W/v3 $W/v4 $W/v1 $W/v2 $W/srv
C="x=iw*0.35:y=ih*0.35:w=iw*0.3:h=ih*0.3"; M="x=iw*0.2:y=ih*0.2:w=iw*0.6:h=ih*0.6"
ROI2="addroi=$C:qoffset=-0.3,addroi=$M:qoffset=-0.15"
# 1. ROI against squish, v3 spin (frames 240-599 of the clip)
[ -f $W/v3/ref.mkv ] || $B ref $F/v3.mp4 $W/v3/ref.mkv 1920 1080 4 6
A="$B arm $W/v3/ref.mkv $W/v3 $S/roi_v3.json"
for k in 6000 3000; do $A plain $k plain; $A squish56 $k squish 1440x810; $A roi10 $k roi "addroi=$C:qoffset=-0.2"; $A roi15 $k roi "addroi=$C:qoffset=-0.3"; done
$A roi5 6000 roi "addroi=$C:qoffset=-0.1"; $A roi2zone 6000 roi "$ROI2"; $A squish80 6000 squish 1712x964
$A squish56+roi10 6000 squish 1440x810 "addroi=x=iw*0.3:y=ih*0.3:w=iw*0.4:h=ih*0.4:qoffset=-0.2"
$A squish56fast 6000 squishfast 1440x810
# control: a plain downscale to the squish's pixel count (and to 720p), upscaled bicubic by the viewer
A="$B arm $W/v3/ref.mkv $W/v3 $S/control_v3.json"
for k in 6000 3000; do $A scale810 $k scale 1440x810; $A scale720 $k scale 1280x720; done
UNSQ_CUBIC=1 $A squish56-cubic-unsquish 6000 squish 1440x810
# 2. Full 102 s mixed-motion clip, per-frame arrays kept for the dynamic policy
[ -f $W/v4/ref.mkv ] || $B ref $F/v4.mp4 $W/v4/ref.mkv 1920 1080
[ -f $W/v4/motion.npy ] || $B motion $W/v4/ref.mkv $W/v4/motion.npy
A="$B arm $W/v4/ref.mkv $W/v4 $S/full_v4.json"
$A plain 6000 plain; $A squish56 6000 squish 1440x810; $A roi2zone 6000 roi "$ROI2"
$A plain 12000 plain; $A squish56 12000 squish 1440x810
$A scale810 6000 scale 1440x810; $A scale810 12000 scale 1440x810
UNSQ_CUBIC=1 $A squish56c 6000 squish 1440x810; UNSQ_CUBIC=1 $A squish56c 12000 squish 1440x810
# 3. Server-side: a 6000 kbit/s ingest of seconds 72-92, transcoded to lower rungs, scored against the pristine source
[ -f $W/srv/ref.mkv ] || ffmpeg -v error -y -ss 72 -t 20 -i $W/v4/ref.mkv -c:v libx264 -preset ultrafast -qp 0 -pix_fmt yuv420p $W/srv/ref.mkv
KEEP=1 $B arm $W/srv/ref.mkv $W/srv $S/server.json ingest 6000 plain
export SCORE_REF=$W/srv/ref.mkv; A="$B arm $W/srv/ingest_6000.mkv $W/srv $S/server.json"
$A scale720 3000 scale 1280x720;  $A squish720 3000 squish 1280x720;  $A plain1080 3000 plain; $A roi1080 3000 roi "$ROI2"
$A scale480 1500 scale 854x480;   $A squish480 1500 squishbase 1280x720 854x480
$A scale360 800 scale 640x360;    $A squish360 800 squishbase 960x540 640x360
# each rung's own picture squished to 56% of its pixels, at the rung's bitrate
$A rung720squish56 3000 squishbase 1280x720 960x540; $A rung480squish56 1500 squishbase 854x480 640x360
$A rung360squish56 800 squishbase 640x360 480x270;   $A squish56of1080 3000 squish 1440x810
unset SCORE_REF
# 4. The two other spin clips
for v in v1 v2; do [ -f $W/$v/ref.mkv ] || $B ref $F/$v.mp4 $W/$v/ref.mkv 1920 1080
  A="$B arm $W/$v/ref.mkv $W/$v $S/spins.json"; $A $v-plain 6000 plain; $A $v-squish56 6000 squish 1440x810; $A $v-scale810 6000 scale 1440x810
  UNSQ_CUBIC=1 $A $v-squish56-cubic-unsquish 6000 squish 1440x810; done
# 5. Other codecs on the v3 spin
for c in x265 svtav1; do for k in 6000 3000; do
  CODEC=$c $B arm $W/v3/ref.mkv $W/v3 $S/codecs.json $c-plain $k plain; CODEC=$c $B arm $W/v3/ref.mkv $W/v3 $S/codecs.json $c-squish56 $k squish 1440x810
  CODEC=$c $B arm $W/v3/ref.mkv $W/v3 $S/codecs.json $c-scale810 $k scale 1440x810; done; done
echo ALLDONE
