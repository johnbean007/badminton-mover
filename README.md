# Badminton Mover

Tracks professional badminton players' footwork from broadcast clips and shows each step as a labelled event (movement, foot, court zone) under a skeleton-overlay video, with the shuttle's flight drawn on top.

- Spec: https://claude.ai/code/artifact/ba76d868-e224-4fb0-a780-af2023ea0a2e
- Clickable prototype: https://claude.ai/artifact/VNZ8Y13NGTtrcXwaWT5VPT

## Status

Spike: checking that pose tracking finds the near-side player's feet well enough in real broadcast footage. See [spike/README.md](spike/README.md).

## Rules for this repo

- No video, pose data or other footage-derived files are ever committed. Broadcast footage is copyrighted.
- No secrets in the repo. Keys live in `.env` files, which are git-ignored.
