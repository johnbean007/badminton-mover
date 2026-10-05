"""Pose spike: track the near-side player's feet in a broadcast clip and detect foot contacts.

Usage (from the spike/ folder):
    uv run pose_spike.py clips/final.mp4
    uv run pose_spike.py clips/final.mp4 --start 12 --end 40 --hand R

If clips/<clip name>.corners.txt exists (made by pick_corners.py), the court is used to
ignore people off court and to give each foot contact a court position and zone.

Outputs, in out/<clip name>/:
    overlay.mp4   the clip with the tracked skeleton, contacts and a contact timeline strip
    pose.json     per-frame keypoints for the tracked player, plus the detected contacts
    summary.md    tracking coverage, foot keypoint confidence, contact counts, speed
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np

# COCO-WholeBody keypoints 0-16 are the body, 17-22 the feet.
N_KP = 23
LEFT = {"ankle": 15, "big_toe": 17, "small_toe": 18, "heel": 19}
RIGHT = {"ankle": 16, "big_toe": 20, "small_toe": 21, "heel": 22}
FOOT_IDX = [15, 16, 17, 18, 19, 20, 21, 22]
LEFT_KP = {5, 7, 9, 11, 13, 15, 17, 18, 19}
RIGHT_KP = {6, 8, 10, 12, 14, 16, 20, 21, 22}
BONES = [
    (5, 6), (11, 12), (5, 11), (6, 12), (0, 5), (0, 6),
    (5, 7), (7, 9), (6, 8), (8, 10),
    (11, 13), (13, 15), (15, 19), (15, 17), (17, 18), (19, 17),
    (12, 14), (14, 16), (16, 22), (16, 20), (20, 21), (22, 20),
]
# BGR colours: left blue, right orange, centre light grey (same pairing as the app)
C_LEFT, C_RIGHT, C_MID = (230, 110, 40), (30, 120, 235), (225, 225, 225)
KP_MIN = 0.3

# Singles court corners in court metres: x = player's right, y = metres from the net towards the near baseline.
COURT_CORNERS = np.float32([[-2.59, 6.7], [2.59, 6.7], [2.59, -6.7], [-2.59, -6.7]])


def zone_of(x: float, y: float, hand: str) -> str:
    col = 0 if x < -0.863 else 2 if x > 0.863 else 1
    row = 0 if y < 2.0 else 1 if y < 4.6 else 2
    left, right = ("BH", "FH") if hand == "R" else ("FH", "BH")
    grid = [[f"Front {left}", "Front C", f"Front {right}"],
            [f"Mid {left}", "Base", f"Mid {right}"],
            [f"Rear {left}", "Rear C", f"Rear {right}"]]
    return grid[row][col]


def load_corners(clip: Path) -> np.ndarray | None:
    f = clip.with_suffix(".corners.txt")
    if not f.exists():
        return None
    pts = [tuple(map(float, p.split(","))) for p in f.read_text().strip().split(";")]
    if len(pts) != 4:
        raise SystemExit(f"{f} should hold 4 points, near-left;near-right;far-right;far-left")
    return np.float32(pts)


def to_court(H: np.ndarray, pt) -> tuple[float, float]:
    p = cv2.perspectiveTransform(np.float32([[pt]]), H)[0, 0]
    return float(p[0]), float(p[1])


def candidate_boxes(bboxes, W, H, prev_center, H_court, keep=2):
    """Keep the few detections that could be the near-side player, so pose runs on them only."""
    scored = []
    for b in bboxes:
        x1, y1, x2, y2 = b[:4]
        if y2 - y1 < 0.08 * H:
            continue
        foot = np.array([(x1 + x2) / 2, y2])
        if H_court is not None:
            cx, cy = to_court(H_court, foot)
            if not (-3.6 <= cx <= 3.6 and -0.3 <= cy <= 8.2):
                continue
        score = y2 / H
        if prev_center is not None:
            score -= 2.0 * np.hypot(*(np.array([(x1 + x2) / 2, (y1 + y2) / 2]) - prev_center)) / W
        scored.append((score, b[:4]))
    scored.sort(key=lambda s: -s[0])
    return np.array([b for _, b in scored[:keep]], np.float32)


def far_player_box(bboxes, H, H_court, prev_far):
    """The opponent's box on the far half (needs the court). Used only to tell who hit the shuttle."""
    if H_court is None:
        return None
    best, best_score = None, -1e9
    for b in bboxes:
        x1, y1, x2, y2 = map(float, b[:4])
        if y2 - y1 < 0.04 * H:
            continue
        cx, cy = to_court(H_court, ((x1 + x2) / 2, y2))
        if not (-3.6 <= cx <= 3.6 and -8.2 <= cy <= 0.3):
            continue
        score = (y2 - y1) / H
        if prev_far is not None:
            score -= np.hypot((x1 + x2) / 2 - (prev_far[0] + prev_far[2]) / 2, y2 - prev_far[3]) / H
        if score > best_score:
            best, best_score = [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)], score
    return best


def pick_near_player(kps, scores, W, H, prev_center, H_court):
    """Return (index, centre, height) of the near-side player, or None."""
    best, best_score = None, -1e9
    for i in range(len(kps)):
        k, s = kps[i][:N_KP], scores[i][:N_KP]
        vis = s > KP_MIN
        if vis.sum() < 8:
            continue
        pts = k[vis]
        (x0, y0), (x1, y1) = pts.min(0), pts.max(0)
        h = y1 - y0
        if h < 0.08 * H:
            continue
        fvis = s[FOOT_IDX] > KP_MIN
        if not fvis.any():
            continue
        foot = k[FOOT_IDX][fvis].mean(0)
        if H_court is not None:
            cx, cy = to_court(H_court, foot)
            if not (-3.6 <= cx <= 3.6 and -0.3 <= cy <= 8.2):
                continue  # off court or on the far half
        centre = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
        score = foot[1] / H  # nearer the camera = lower in frame
        if prev_center is not None:
            score -= 2.0 * np.hypot(*(centre - prev_center)) / W
        elif not (0.15 * W < centre[0] < 0.85 * W):
            score -= 0.5  # line judges and coaches sit at the edges
        if score > best_score:
            best, best_score = (i, centre, h), score
    return best


def smooth(series: np.ndarray, win: int) -> np.ndarray:
    """Centred moving average along axis 0 that ignores NaNs."""
    if win <= 1:
        return series
    out = np.full_like(series, np.nan)
    half = win // 2
    for t in range(len(series)):
        chunk = series[max(0, t - half): t + half + 1]
        if np.isfinite(chunk).any():
            out[t] = np.nanmean(chunk, axis=0)
    return out


def ground_point(track: np.ndarray, conf: np.ndarray, foot: dict) -> np.ndarray:
    """Per-frame ground point of one foot: mean of heel and big toe, falling back to the ankle."""
    T = len(track)
    g = np.full((T, 2), np.nan, np.float32)
    for t in range(T):
        idx = [foot["heel"], foot["big_toe"]]
        good = [i for i in idx if conf[t, i] > KP_MIN]
        if good:
            g[t] = track[t, good].mean(0)
        elif conf[t, foot["ankle"]] > KP_MIN:
            g[t] = track[t, foot["ankle"]]
    return g


def detect_contacts(track, conf, heights, fps, still_thresh, H_court, hand, start_frame):
    """A contact = a run of frames where the foot's ground point is (nearly) stationary."""
    min_len = max(2, round(0.04 * fps))
    contacts = []
    for name, foot in (("L", LEFT), ("R", RIGHT)):
        g = smooth(ground_point(track, conf, foot), 3)
        v = np.full(len(g), np.nan)
        v[1:] = np.linalg.norm(np.diff(g, axis=0), axis=1) * fps / heights[1:]  # body heights per second
        still = np.nan_to_num(v, nan=99) < still_thresh
        t = 0
        while t < len(still):
            if not still[t]:
                t += 1
                continue
            t1 = t
            while t1 + 1 < len(still) and (still[t1 + 1] or (t1 + 2 < len(still) and still[t1 + 2])):
                t1 += 1
            if t1 - t + 1 >= min_len:
                pt = np.nanmedian(g[t:t1 + 1], axis=0)
                c = {"foot": name, "f0": start_frame + t, "f1": start_frame + t1,
                     "t0": round((start_frame + t) / fps, 3), "t1": round((start_frame + t1) / fps, 3),
                     "image_xy": [round(float(pt[0]), 1), round(float(pt[1]), 1)]}
                if H_court is not None and np.isfinite(pt).all():
                    cx, cy = to_court(H_court, pt)
                    c["court_xy_m"] = [round(cx, 2), round(cy, 2)]
                    c["zone"] = zone_of(cx, cy, hand)
                contacts.append(c)
            t = t1 + 1
    contacts.sort(key=lambda c: c["f0"])
    return contacts


def open_writer(path: Path, w: int, h: int, fps: float) -> subprocess.Popen:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{w}x{h}", "-r", f"{fps}", "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-crf", "22", "-movflags", "+faststart", str(path)]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def draw_frame(frame, kp, cf, in_contact, contacts, f_abs, f_first, f_last, fps):
    H, W = frame.shape[:2]
    if kp is not None:
        for a, b in BONES:
            if cf[a] > KP_MIN and cf[b] > KP_MIN:
                col = C_LEFT if (a in LEFT_KP and b in LEFT_KP) else C_RIGHT if (a in RIGHT_KP and b in RIGHT_KP) else C_MID
                cv2.line(frame, tuple(map(int, kp[a])), tuple(map(int, kp[b])), col, 2, cv2.LINE_AA)
        for i in range(N_KP):
            if cf[i] <= KP_MIN:
                continue
            col = C_LEFT if i in LEFT_KP else C_RIGHT if i in RIGHT_KP else C_MID
            p = tuple(map(int, kp[i]))
            if i in FOOT_IDX:
                down = in_contact["L"] if i in LEFT_KP else in_contact["R"]
                if down:
                    cv2.circle(frame, p, 6, col, -1, cv2.LINE_AA)
                    cv2.circle(frame, p, 6, (255, 255, 255), 1, cv2.LINE_AA)
                else:
                    cv2.circle(frame, p, 6, col, 2, cv2.LINE_AA)
            else:
                cv2.circle(frame, p, 3, col, -1, cv2.LINE_AA)
    # status box
    cv2.rectangle(frame, (16, 16), (300, 92), (20, 20, 20), -1)
    cv2.putText(frame, f"{f_abs / fps:6.2f} s  frame {f_abs}", (28, 42), cv2.FONT_HERSHEY_SIMPLEX, .6, (240, 240, 240), 1, cv2.LINE_AA)
    for j, (name, col) in enumerate((("L", C_LEFT), ("R", C_RIGHT))):
        x = 28 + j * 130
        state = "DOWN" if in_contact[name] else "air"
        cv2.putText(frame, f"{name} {state}", (x, 76), cv2.FONT_HERSHEY_SIMPLEX, .7, col, 2 if in_contact[name] else 1, cv2.LINE_AA)
    if kp is None:
        cv2.putText(frame, "player not found", (28, 120), cv2.FONT_HERSHEY_SIMPLEX, .7, (60, 60, 230), 2, cv2.LINE_AA)
    # timeline strip under the frame
    strip = np.full((70, W, 3), 24, np.uint8)
    span = max(1, f_last - f_first)
    X = lambda f: int(10 + (f - f_first) / span * (W - 20))
    for c in contacts:
        y = 14 if c["foot"] == "L" else 40
        col = C_LEFT if c["foot"] == "L" else C_RIGHT
        cv2.rectangle(strip, (X(c["f0"]), y), (max(X(c["f0"]) + 2, X(c["f1"])), y + 16), col, -1)
    cv2.line(strip, (X(f_abs), 4), (X(f_abs), 66), (80, 80, 240), 2)
    return np.vstack([frame, strip])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clip", type=Path)
    ap.add_argument("--start", type=float, default=0, help="seconds")
    ap.add_argument("--end", type=float, default=None, help="seconds")
    ap.add_argument("--hand", choices=["R", "L"], default="R", help="tracked player's handedness, for zones")
    ap.add_argument("--mode", choices=["lightweight", "balanced", "performance"], default="balanced")
    ap.add_argument("--still", type=float, default=0.5, help="foot speed below this (body heights/s) counts as a contact")
    args = ap.parse_args()

    from rtmlib import Wholebody  # imported here so --help works before models are downloaded

    cap = cv2.VideoCapture(str(args.clip))
    if not cap.isOpened():
        raise SystemExit(f"Can't open {args.clip}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    f_first = int(args.start * fps)
    f_last = min(n_total - 1, int(args.end * fps)) if args.end else n_total - 1
    corners = load_corners(args.clip)
    H_court = cv2.getPerspectiveTransform(corners, COURT_CORNERS) if corners is not None else None

    out_dir = Path("out") / args.clip.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"{args.clip.name}: {W}x{H} at {fps:.2f} fps, frames {f_first}-{f_last}, court {'set' if H_court is not None else 'not set'}")

    model = Wholebody(mode=args.mode, backend="onnxruntime", device="cpu")

    # Pass 1: pose on every frame, keep the near-side player
    T = f_last - f_first + 1
    track = np.full((T, N_KP, 2), np.nan, np.float32)
    conf = np.zeros((T, N_KP), np.float32)
    heights = np.full(T, np.nan, np.float32)
    cap.set(cv2.CAP_PROP_POS_FRAMES, f_first)
    prev_center, prev_far, t_start = None, None, time.time()
    far_boxes: list = [None] * T
    far_kp = np.full((T, N_KP, 2), np.nan, np.float32)
    far_conf = np.zeros((T, N_KP), np.float32)
    for t in range(T):
        ok, frame = cap.read()
        if not ok:
            T = t
            break
        dets = model.det_model(frame)
        far_boxes[t] = prev_far = far_player_box(dets, H, H_court, prev_far)
        boxes = candidate_boxes(dets, W, H, prev_center, H_court)
        if far_boxes[t] is not None:  # pose the opponent too, in the same call, to see their racket swing
            boxes = np.vstack([boxes.reshape(-1, 4), np.float32([far_boxes[t]])])
        kps, scs = model.pose_model(frame, bboxes=boxes) if len(boxes) else ([], [])
        if far_boxes[t] is not None and len(kps):
            far_kp[t], far_conf[t] = kps[-1][:N_KP], scs[-1][:N_KP]
            kps, scs = kps[:-1], scs[:-1]
        pick = pick_near_player(kps, scs, W, H, prev_center, H_court) if len(kps) else None
        if pick:
            i, prev_center, h = pick
            track[t], conf[t], heights[t] = kps[i][:N_KP], scs[i][:N_KP], h
        else:
            prev_center = None if t and np.isnan(heights[max(0, t - int(fps)):t]).all() else prev_center
        if t % 50 == 0 and t:
            rate = t / (time.time() - t_start)
            print(f"  pose {t}/{T} frames  {rate:.1f} fps  ~{(T - t) / rate:.0f} s left", flush=True)
    track, conf, heights = track[:T], conf[:T], heights[:T]
    pose_secs = time.time() - t_start
    f_last = f_first + T - 1

    # Steady player height for normalising foot speed
    hs = heights.copy()
    for t in range(T):
        win = heights[max(0, t - 7): t + 8]
        hs[t] = np.nanmedian(win) if np.isfinite(win).any() else np.nan
    hs = np.where(np.isfinite(hs), hs, np.nanmedian(heights) if np.isfinite(heights).any() else 1.0)

    contacts = detect_contacts(track, conf, hs, fps, args.still, H_court, args.hand, f_first)

    # Pass 2: render the overlay video
    print("  rendering overlay.mp4 …", flush=True)
    writer = open_writer(out_dir / "overlay.mp4", W, H + 70, fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, f_first)
    for t in range(T):
        ok, frame = cap.read()
        if not ok:
            break
        f_abs = f_first + t
        down = {n: any(c["foot"] == n and c["f0"] <= f_abs <= c["f1"] for c in contacts) for n in ("L", "R")}
        found = np.isfinite(track[t]).all() and conf[t].max() > 0
        img = draw_frame(frame, track[t] if found else None, conf[t], down, contacts, f_abs, f_first, f_last, fps)
        writer.stdin.write(img.tobytes())
    writer.stdin.close()
    writer.wait()
    cap.release()

    # Results
    found = np.isfinite(heights)
    foot_conf = conf[found][:, FOOT_IDX] if found.any() else np.zeros((0, len(FOOT_IDX)))
    durs = [(c["f1"] - c["f0"] + 1) / fps * 1000 for c in contacts]
    n_l = sum(c["foot"] == "L" for c in contacts)
    n_r = len(contacts) - n_l
    zones = {}
    for c in contacts:
        if "zone" in c:
            zones[c["zone"]] = zones.get(c["zone"], 0) + 1
    summary = {
        "clip": args.clip.name, "resolution": f"{W}x{H}", "fps": round(fps, 2),
        "frames": T, "seconds": round(T / fps, 2), "mode": args.mode,
        "player_found_pct": round(100 * found.mean(), 1),
        "foot_keypoint_conf_mean": round(float(foot_conf.mean()), 3) if foot_conf.size else None,
        "foot_keypoints_above_threshold_pct": round(100 * float((foot_conf > KP_MIN).mean()), 1) if foot_conf.size else None,
        "contacts_left": n_l, "contacts_right": n_r,
        "contacts_per_second": round(len(contacts) / (T / fps), 2) if T else 0,
        "median_contact_ms": round(float(np.median(durs))) if durs else None,
        "pose_processing_fps": round(T / pose_secs, 1),
        "zones": zones,
    }
    (out_dir / "pose.json").write_text(json.dumps({
        "summary": summary, "keypoint_format": "COCO-WholeBody 0-22, [x, y] image px", "start_frame": f_first,
        "frames": [{"f": f_first + t, "kp": None if not found[t] else np.round(track[t], 1).tolist(),
                    "conf": None if not found[t] else np.round(conf[t], 3).tolist(),
                    "far_box": far_boxes[t],
                    "far_kp": None if far_boxes[t] is None else np.round(far_kp[t], 1).tolist(),
                    "far_conf": None if far_boxes[t] is None else np.round(far_conf[t], 3).tolist()} for t in range(T)],
        "contacts": contacts,
    }))
    lines = [f"# Spike results: {args.clip.name}", "",
             "| Measure | Value |", "| --- | --- |"]
    labels = {
        "resolution": "Resolution", "fps": "Frame rate", "seconds": "Length analysed (s)", "mode": "Pose model mode",
        "player_found_pct": "Frames with the near player found (%)",
        "foot_keypoint_conf_mean": "Mean foot keypoint confidence (0-1)",
        "foot_keypoints_above_threshold_pct": f"Foot keypoints above {KP_MIN} confidence (%)",
        "contacts_left": "Left-foot contacts", "contacts_right": "Right-foot contacts",
        "contacts_per_second": "Contacts per second", "median_contact_ms": "Median contact length (ms)",
        "pose_processing_fps": "Pose processing speed (frames/s)",
    }
    for k, label in labels.items():
        lines.append(f"| {label} | {summary[k]} |")
    if zones:
        lines += ["", "Contacts by zone: " + ", ".join(f"{z} {n}" for z, n in sorted(zones.items(), key=lambda x: -x[1]))]
    lines += ["", "Watch overlay.mp4 at slow speed (QuickTime: Option-click the fast-forward button) and note:",
              "- Does the skeleton stay on the near player the whole time?",
              "- Do the heel and toe dots sit on the shoes, including during lunges and jumps?",
              "- Are left and right ever swapped?",
              "- Does each filled dot (DOWN) match a real foot plant? Any plants missed or extra?"]
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[2:2 + len(labels) + 2]))
    print(f"\nWrote {out_dir}/overlay.mp4, pose.json, summary.md")


if __name__ == "__main__":
    main()
