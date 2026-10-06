# Badminton Mover

Tracks professional badminton players' footwork from broadcast clips and shows each step as a labelled event (movement, foot, court zone) under a skeleton-overlay video, with the shuttle's flight drawn on top.

- Spec: https://claude.ai/code/artifact/ba76d868-e224-4fb0-a780-af2023ea0a2e
- Clickable prototype: https://claude.ai/artifact/VNZ8Y13NGTtrcXwaWT5VPT

## Status

- Milestone 1 (foundations): invite-only sign-in, Vercel deploy, private R2 video bucket, Modal worker, CI. Done.
- Milestone 2 (upload and library): direct upload to R2, clip details, filters, delete. Done.
- Milestone 3 (pre-scan and rally review): playback copy, camera-cut rally proposals, segment review, court calibration, shirt colour, near-side check. Done; a 5-minute London 2012 broadcast clip gave 11 correct segments with no fixes.
- Milestone 4 (pose and viewer): GPU analysis on Modal (RTMW pose of both players, TrackNetV3 shuttle tracking, hit detection) and the viewer with skeleton overlay, shuttle trail, hit lane and speed control. Done; the same 5-minute clip analysed in 10 minutes, and the overlay stayed on the right player at 0.1× and 1×.
- Milestone 5 (contacts and zones): foot contacts from stored pose, court metres and nine zones, left/right foot lanes, court-fit check with automatic snapping and per-rally recalibration. Done; contacts and zones checked on three rallies of the test clip.
- Next: milestone 6, rule-based movement labels.

## Layout

- `web/`: Next.js app (Vercel). Copy `web/.env.example` to `web/.env.local`.
- `worker/`: GPU worker on Modal. `cd worker && uv sync && .venv/bin/modal run app.py`
- `supabase/`: database migrations.
- `spike/`: the pose and shuttle tracking experiments. See [spike/README.md](spike/README.md).

## Rules for this repo

- No video, pose data or other footage-derived files are ever committed. Broadcast footage is copyrighted.
- No secrets in the repo. Keys live in `.env` files, which are git-ignored.
