"""Hand-label one rally's movements, then score the rules against those labels.

    uv run python label_sheet.py make  data/<clip_id> --rally 5    # label_me.mp4 + label_me.csv in data/<clip_id>/rally5/
    uv run python label_sheet.py score data/<clip_id> --rally 5    # after filling in the csv's `movement` column

Needs the clip's data from `modal run app.py::pull_now --clip-id <uuid> --video`. The video plays at
half speed with each foot contact's number drawn at the foot while it is planted (left blue, right
orange) and the shuttle hits flashed; the rules' own labels are not shown, so they don't steer you.
In the csv, give each contact the movement it belongs to (any of the names or short codes below);
both landing contacts of a split step or jump get the same label; blank = skip, "none" = not a step.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
from pathlib import Path

import cv2
import numpy as np

import contacts as ct
import movements as mv

CODES = {"split": "split_step", "chasse": "chasse", "chassé": "chasse", "cross": "cross_step", "run": "running_step", "running": "running_step",
         "lunge": "lunge", "scissor": "scissor_jump", "jump": "jump", "pivot": "pivot", "turn": "pivot", "recovery": "recovery_step",
         "recover": "recovery_step", "hit": "hitting_step", "hitting": "hitting_step", "none": "none"}
COLOUR = {"L": (230, 123, 47), "R": (36, 138, 240)}  # BGR, as the viewer's blue/orange


def norm(label: str) -> str | None:
    s = label.strip().lower().replace("-", " ").replace("_", " ")
    if not s:
        return None
    if s.replace(" ", "_") in mv.TYPES:
        return s.replace(" ", "_")
    first = s.split()[0]
    if first in CODES:
        return CODES[first]
    raise SystemExit(f"Unknown movement label {label!r}; use one of: {', '.join(sorted(set(CODES)))}")


def rally(folder: Path, number: int):
    meta = json.loads((folder / "clip.json").read_text())
    r = next(r for r in meta["rallies"] if r["index"] == number - 1)
    pose = ct.load_pose((folder / f"{r['id']}.npz").read_bytes())
    H = np.array(r["homography"], float)
    fps = float(meta["fps"])
    found = ct.detect(pose["kp"], pose["conf"], fps, int(pose["frame"][0]), H, meta["hand"])
    return meta, r, pose, H, fps, found


def make(folder: Path, number: int) -> None:
    meta, r, pose, H, fps, found = rally(folder, number)
    out = folder / f"rally{number}"
    out.mkdir(exist_ok=True)
    first = int(pose["frame"][0])
    with open(out / "label_me.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["n", "time_s", "foot", "zone", "movement"])
        for n, c in enumerate(found, 1):
            w.writerow([n, f"{(c['start_frame'] - r['start_frame']) / fps:.2f}", c["foot"], c["zone"], ""])

    cap = cv2.VideoCapture(str(folder / "playback.mp4"))
    W, Hh = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.set(cv2.CAP_PROP_POS_FRAMES, first)
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{Hh}", "-r", str(fps / 2),
                           "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", str(out / "label_me.mp4")], stdin=subprocess.PIPE)
    hits = {h["frame"]: h["hitter"] for h in r["hits"]}
    font = cv2.FONT_HERSHEY_SIMPLEX
    for t in range(len(pose["kp"])):
        ok, img = cap.read()
        if not ok:
            break
        f = first + t
        for n, c in enumerate(found, 1):
            if c["start_frame"] <= f <= c["end_frame"]:
                x, y = int(c["image_xy"][0] * W), int(c["image_xy"][1] * Hh)
                cv2.circle(img, (x, y), 6, COLOUR[c["foot"]], -1)
                label = str(n)
                (tw, th), _ = cv2.getTextSize(label, font, 0.8, 2)
                tx, ty = x - tw // 2, y + 30 + th
                cv2.rectangle(img, (tx - 4, ty - th - 4), (tx + tw + 4, ty + 6), (0, 0, 0), -1)
                cv2.putText(img, label, (tx, ty), font, 0.8, COLOUR[c["foot"]], 2, cv2.LINE_AA)
        flash = [who for hf, who in hits.items() if 0 <= f - hf < 4]
        if flash:
            cv2.putText(img, "HIT: " + ("PLAYER" if flash[0] == "player" else "opponent"), (20, 90), font, 1.0, (74, 216, 255), 2, cv2.LINE_AA)
        cv2.rectangle(img, (10, 10), (330, 55), (0, 0, 0), -1)
        cv2.putText(img, f"rally {number}  {(f - r['start_frame']) / fps:6.2f} s", (20, 42), font, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
        ff.stdin.write(img.tobytes())
    ff.stdin.close()
    ff.wait()
    print(f"{len(found)} contacts → {out / 'label_me.mp4'} and {out / 'label_me.csv'}")


def score(folder: Path, number: int) -> None:
    meta, r, pose, H, fps, found = rally(folder, number)
    rows = list(csv.DictReader(open(folder / f"rally{number}" / "label_me.csv")))
    if len(rows) != len(found):
        raise SystemExit(f"The csv has {len(rows)} contacts but the rules now find {len(found)}; contacts changed since it was made")
    moves = mv.label(pose["kp"], pose["conf"], fps, int(pose["frame"][0]), found, r["hits"], H, meta["hand"], mv.load_rules(),
                     pose["meta"]["width"], pose["meta"]["height"])
    predicted = {i: m["movement_type"] for m in moves for i in m["contacts"]}
    pairs = [(norm(row["movement"]), predicted.get(i, "none"), row) for i, row in enumerate(rows)]
    pairs = [p for p in pairs if p[0] is not None]
    if not pairs:
        raise SystemExit("No labels in the csv yet")
    right = sum(a == b for a, b, _ in pairs)
    print(f"rally {number}: {right}/{len(pairs)} contacts labelled the same ({100 * right / len(pairs):.0f}%; target ≥ 70%)\n")
    kinds = sorted({a for a, _, _ in pairs} | {b for _, b, _ in pairs})
    width = max(16, max(len(k) for k in kinds) + 1)
    print("you ↓  rules →".ljust(width) + " ".join(k[:6].rjust(6) for k in kinds))
    for a in kinds:
        print(a.ljust(width) + " ".join(str(sum(1 for x, y, _ in pairs if x == a and y == b) or ".").rjust(6) for b in kinds))
    print("\nDisagreements:")
    for a, b, row in pairs:
        if a != b:
            print(f"  #{row['n']:>3} {row['time_s']:>6} s {row['foot']} {row['zone']:<8}  you: {a:<14} rules: {b}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["make", "score"])
    ap.add_argument("folder", type=Path)
    ap.add_argument("--rally", type=int, required=True, help="rally number as the viewer shows it (1 = first)")
    a = ap.parse_args()
    (make if a.action == "make" else score)(a.folder, a.rally)
