# Spike: can pose tracking see the feet?

A throwaway experiment, run locally, to test the riskiest assumption before building the app: that a CPU pose model finds the near-side player's feet well enough in broadcast footage to detect foot contacts.

Part 1 (this folder): pose and foot contacts. Part 2 (next): shuttle tracking.

## Setup

```bash
cd ~/Documents/badminton-mover/spike
uv sync
```

The first run also downloads the pose models (via rtmlib) into `~/.cache`.

## Run

1. Put 2–3 clips in `clips/`: pro singles, main camera, 30–60 s each. Ideally one at 50/60 fps and one at 25/30 fps.
2. Optional: mark the court so contacts get zones and off-court people are ignored. In your own Terminal:
   `uv run pick_corners.py clips/<clip>.mp4`
3. Run the spike:
   `uv run pose_spike.py clips/<clip>.mp4 --hand R`
   Add `--start 10 --end 40` to analyse part of a clip.
4. Open `out/<clip>/overlay.mp4` and read `out/<clip>/summary.md`.

## What counts as a pass

- The skeleton stays on the near player through whole rallies.
- Heel and toe points sit on the shoes, including during lunges and jumps.
- Left and right are rarely swapped.
- Most real foot plants show as contacts, with few extras.

If these hold at 50 fps but not at 25 fps, the app should prefer 50/60 fps sources. If they don't hold at all, we revisit the plan before building.

## Part 2: shuttle and hits

```bash
./setup_tracknet.sh
cd vendor/TrackNetV3
uv run --project ../.. python predict.py --video_file ../../clips/<clip>.mp4 \
  --tracknet_file ckpts/TrackNet_best.pt --inpaintnet_file ckpts/InpaintNet_best.pt \
  --save_dir ../../out/<clip>/shuttle
cd ../..
uv run shuttle_hits.py clips/<clip>.mp4      # writes out/<clip>/combined.mp4 and hits.json
uv run eval_hits.py clips/<clip>.mp4         # needs clips/<clip>.hits.txt: "<seconds> <LD|LCW>" per line
```

## Results (5 October 2026)

London 2012 final, Lin Dan (near side, left-handed) v Lee Chong Wei, screen recording at about 30 fps.

| | Rally 1 (23 s, tuned on) | Rally 2 (15 s, held-out) |
| --- | --- | --- |
| Near player found | 100% of frames | 100% of frames |
| Shuttle found | 96.8% of frames | 83.0% of frames |
| Hits found (vs hand labels) | 21 of 23 | 12 of 13 |
| False hits | 1 | 2 |
| Hitter correct | 21 of 21 | 12 of 12 |

Speed on an M5 Pro: pose (lightweight) 14 frames/s on CPU; shuttle 8 frames/s on the GPU but about 2 frames/s on CPU, so the production worker needs a GPU.

Not yet checked: whether detected foot contacts match real foot plants. That needs the gold set.
