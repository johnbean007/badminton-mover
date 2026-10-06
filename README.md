# Badminton Mover

Tracks professional badminton players' footwork from broadcast clips and shows each step as a labelled event (movement, foot, court zone) under a skeleton-overlay video, with the shuttle's flight drawn on top.

- Spec: https://claude.ai/code/artifact/ba76d868-e224-4fb0-a780-af2023ea0a2e
- Clickable prototype: https://claude.ai/artifact/VNZ8Y13NGTtrcXwaWT5VPT

## Status

Milestone 1 (foundations) done: invite-only sign-in, Vercel deploy, private R2 video bucket, Modal GPU worker skeleton, CI.

## Layout

- `web/`: Next.js app (Vercel). Copy `web/.env.example` to `web/.env.local`.
- `worker/`: GPU worker on Modal. `cd worker && uv sync && .venv/bin/modal run app.py`
- `supabase/`: database migrations.
- `spike/`: the pose and shuttle tracking experiments. See [spike/README.md](spike/README.md).

## Rules for this repo

- No video, pose data or other footage-derived files are ever committed. Broadcast footage is copyrighted.
- No secrets in the repo. Keys live in `.env` files, which are git-ignored.
