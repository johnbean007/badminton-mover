"""Score shuttle hit detection against hand labels (clips/<clip>.hits.txt).

    uv run eval_hits.py clips/lindan_lcw_r1.mp4 [--tol 0.25] [--sweep]
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

from shuttle_hits import dedupe_and_fill, find_hits, load_shuttle


def load_labels(path: Path):
    out = []
    for line in path.read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            t, who = line.split()[:2]
            out.append((float(t), who))
    return out


def score(hits, labels, near_code, tol):
    used, matched, who_ok, offsets = set(), 0, 0, []
    for t, who in labels:
        best = None
        for i, h in enumerate(hits):
            if i not in used and abs(h["t"] - t) <= tol and (best is None or abs(h["t"] - t) < abs(hits[best]["t"] - t)):
                best = i
        if best is not None:
            used.add(best)
            matched += 1
            offsets.append(hits[best]["t"] - t)
            who_ok += (hits[best]["who"] == "near player") == (who == near_code)
    return {"labelled": len(labels), "detected": len(hits), "found": matched, "false": len(hits) - matched,
            "hitter_correct": who_ok, "mean_offset_s": round(sum(offsets) / len(offsets), 3) if offsets else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip", type=Path)
    ap.add_argument("--tol", type=float, default=0.25, help="seconds; hand labels are to the nearest 0.1 s")
    ap.add_argument("--near", default="LD", help="label code for the near-side player")
    ap.add_argument("--sweep", action="store_true")
    args = ap.parse_args()
    out_dir = Path("out") / args.clip.stem
    pose = json.loads((out_dir / "pose.json").read_text())
    frames, f_first, fps = pose["frames"], pose["start_frame"], pose["summary"]["fps"]
    xy = dedupe_and_fill(load_shuttle(out_dir / "shuttle" / f"{args.clip.stem}_ball.csv", f_first + len(frames))[f_first:])
    labels = load_labels(args.clip.with_suffix(".hits.txt"))

    hits = find_hits(frames, xy, fps, f_first)
    print("Default settings:", score(hits, labels, args.near, args.tol))
    for h in hits:
        lab = min(labels, key=lambda l: abs(l[0] - h["t"]))
        flag = "" if abs(lab[0] - h["t"]) <= args.tol and ((h["who"] == "near player") == (lab[1] == args.near)) else "  <-- check"
        print(f"  {h['t']:6.2f}  {h['who']:12s} swing {h['swing']:5.2f} far {h['swing_far'] if h['swing_far'] is not None else '  -':>5}  label {lab[0]:5.1f} {lab[1]}{flag}")
    missed = [l for l in labels if not any(abs(h["t"] - l[0]) <= args.tol for h in hits)]
    print("Missed:", missed)
    if args.sweep:
        rows = []
        for rise, turn in itertools.product([15, 20, 25, 35, 50], [60, 75, 90, 110, 140]):
            r = score(find_hits(frames, xy, fps, f_first, rise_px=rise, turn_deg=turn), labels, args.near, args.tol)
            rows.append((r["found"] - r["false"], r["hitter_correct"], rise, turn, r))
        rows.sort(key=lambda x: (-x[0], -x[1]))
        print("\nTop settings (found minus false, then hitter correct):")
        for net, ok, rise, turn, r in rows[:8]:
            print(f"  rise {rise:3d}px turn {turn:3d}deg  found {r['found']}/{r['labelled']} false {r['false']} hitter {r['hitter_correct']}/{r['found']}")


if __name__ == "__main__":
    main()
