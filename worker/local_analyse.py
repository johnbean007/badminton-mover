"""Run the analysis on a local clip, score the hits against hand labels, and render a check video.

    uv run --group analyse python local_analyse.py ../spike/clips/lindan_lcw_r1.mp4 \
        --singles-corners ../spike/clips/lindan_lcw_r1.corners.txt \
        --labels ../spike/clips/lindan_lcw_r1.hits.txt --near LD --out /tmp/check

    --overlay <file.json>  render the check video from an overlay file the worker wrote instead
                           (with --start-frame), to see exactly what the viewer gets.

Needs the TrackNetV3 checkpoints (spike/setup_tracknet.sh puts them in spike/vendor/TrackNetV3/ckpts).
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import cv2
import numpy as np

import analyse as an

HERE = Path(__file__).parent
SINGLES = np.float32([[-2.59, 6.7], [2.59, 6.7], [2.59, -6.7], [-2.59, -6.7]])
BONES = [
    (5, 6), (11, 12), (5, 11), (6, 12), (0, 5), (0, 6), (5, 7), (7, 9), (6, 8), (8, 10),
    (11, 13), (13, 15), (15, 19), (15, 17), (17, 18), (19, 17), (12, 14), (14, 16), (16, 22), (16, 20), (20, 21), (22, 20),
]
LEFT_KP = {5, 7, 9, 11, 13, 15, 17, 18, 19}
RIGHT_KP = {6, 8, 10, 12, 14, 16, 20, 21, 22}
C_LEFT, C_RIGHT, C_MID, C_SHUTTLE = (230, 110, 40), (30, 120, 235), (225, 225, 225), (74, 216, 255)


def homography_from_singles(corners_file: Path, w: int, h: int) -> np.ndarray:
    """The spike clicked singles corners in pixels; the app stores frame fractions → court metres."""
    pts = np.float32([tuple(map(float, p.split(","))) for p in corners_file.read_text().strip().split(";")])
    return cv2.getPerspectiveTransform(pts / np.float32([w, h]), SINGLES).astype(float)


def load_labels(path: Path):
    out = []
    for line in path.read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            t, who = line.split()[:2]
            out.append((float(t), who))
    return out


def score(hits, labels, near_code, tol=0.25):
    used, found, who_ok, offsets = set(), 0, 0, []
    for t, who in labels:
        best = None
        for i, h in enumerate(hits):
            if i not in used and abs(h["t"] - t) <= tol and (best is None or abs(h["t"] - t) < abs(hits[best]["t"] - t)):
                best = i
        if best is not None:
            used.add(best)
            found += 1
            offsets.append(hits[best]["t"] - t)
            who_ok += (hits[best]["hitter"] == "player") == (who == near_code)
    return {"labelled": len(labels), "detected": len(hits), "found": found, "false": len(hits) - found,
            "hitter_correct": who_ok, "mean_offset_s": round(sum(offsets) / len(offsets), 3) if offsets else None}


def render(clip: str, start: int, kp_frac, conf, shuttle_frac, hits, fps: float, out: Path):
    """Skeleton, shuttle trail and hit markers drawn over the clip, like the viewer will."""
    cap = cv2.VideoCapture(clip)
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                           "-r", str(fps), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23", str(out)],
                          stdin=subprocess.PIPE)
    trail = round(0.4 * fps)
    P = lambda p: (int(p[0] * W), int(p[1] * H))  # noqa: E731
    for t in range(len(kp_frac)):
        ok, img = cap.read()
        if not ok:
            break
        if np.isfinite(kp_frac[t]).all():
            for a, b in BONES:
                col = C_LEFT if a in LEFT_KP and b in LEFT_KP else C_RIGHT if a in RIGHT_KP and b in RIGHT_KP else C_MID
                thick = 2 if conf[t, a] > an.KP_MIN and conf[t, b] > an.KP_MIN else 1
                cv2.line(img, P(kp_frac[t, a]), P(kp_frac[t, b]), col, thick, cv2.LINE_AA)
            for i in an.FOOT_IDX:
                cv2.circle(img, P(kp_frac[t, i]), 5, C_LEFT if i in LEFT_KP else C_RIGHT, 2, cv2.LINE_AA)
        pts = [shuttle_frac[s] for s in range(max(0, t - trail), t + 1) if np.isfinite(shuttle_frac[s]).all()]
        for p0, p1 in zip(pts, pts[1:]):
            cv2.line(img, P(p0), P(p1), C_SHUTTLE, 2, cv2.LINE_AA)
        if np.isfinite(shuttle_frac[t]).all():
            cv2.circle(img, P(shuttle_frac[t]), 9, C_SHUTTLE, 2, cv2.LINE_AA)
        for h in hits:
            if 0 <= t - h["index"] < round(0.5 * fps) and np.isfinite(shuttle_frac[h["index"]]).all():
                p = P(shuttle_frac[h["index"]])
                cv2.circle(img, p, 22, C_SHUTTLE if h["hitter"] == "player" else C_MID, 3, cv2.LINE_AA)
                cv2.putText(img, h["hitter"], (p[0] + 26, p[1] + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.7, C_MID, 2, cv2.LINE_AA)
        cv2.putText(img, f"frame {start + t}  {(start + t) / fps:6.2f} s", (20, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
        ff.stdin.write(img.tobytes())
    ff.stdin.close()
    ff.wait()
    cap.release()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clip")
    ap.add_argument("--singles-corners", type=Path, help="spike corners file (singles corners, pixels)")
    ap.add_argument("--homography", help="JSON 3×3, frame fractions → court metres (as stored in calibrations)")
    ap.add_argument("--labels", type=Path)
    ap.add_argument("--near", default="LD")
    ap.add_argument("--shirt", default=None, help="#rrggbb")
    ap.add_argument("--start-frame", type=int, default=0)
    ap.add_argument("--end-frame", type=int, default=None)
    ap.add_argument("--overlay", type=Path)
    ap.add_argument("--ckpts", default=str(HERE.parent / "spike/vendor/TrackNetV3/ckpts"))
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(args.clip)
    fps, W, Hh, n = cap.get(cv2.CAP_PROP_FPS), int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)), int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    end = args.end_frame if args.end_frame is not None else n - 1

    if args.overlay:
        o = json.loads(args.overlay.read_text())
        kp = np.array([np.array(r).reshape(23, 3) if r else np.full((23, 3), np.nan) for r in o["kp"]], float)
        sh = np.array([r if r else [np.nan, np.nan] for r in o["shuttle"]], float) / 10000
        render(args.clip, o["start"], kp[:, :, :2] / 10000, kp[:, :, 2] / 100, sh, [], o["fps"], args.out / "overlay_check.mp4")
        print(f"Wrote {args.out / 'overlay_check.mp4'}")
        return

    H = homography_from_singles(args.singles_corners, W, Hh) if args.singles_corners else np.array(json.loads(args.homography), float)
    from tracknet import ShuttleTracker

    pose = an.PoseModel(device="cpu")
    tracker = ShuttleTracker(args.ckpts)
    print(f"{args.clip}: {W}x{Hh} at {fps:.2f} fps, frames {args.start_frame}-{end}; pose on {pose.providers[0]}, shuttle on {tracker.device}")
    r = an.analyse_rally(args.clip, args.start_frame, end, fps, H, args.shirt, pose, tracker)
    print(json.dumps(r.stats))
    for h in r.hits:
        h["t"] = round((r.start_frame + h["index"]) / fps, 3)
    if args.labels:
        print("Hits vs labels:", score(r.hits, load_labels(args.labels), args.near))
    for h in r.hits:
        print(f"  {h['t']:6.2f} s  {h['hitter']:8s} confidence {h['confidence']:.2f}")
    (args.out / "pose.npz").write_bytes(an.pose_npz(r, {}))
    (args.out / "shuttle.npz").write_bytes(an.shuttle_npz(r, {}))
    (args.out / "overlay.json").write_bytes(an.overlay_json(r))
    (args.out / "hits.json").write_text(json.dumps(r.hits, indent=1))
    frac = np.float32([r.width, r.height])
    render(args.clip, r.start_frame, r.kp / frac, r.conf, r.shuttle / frac, r.hits, fps, args.out / "check.mp4")
    print(f"Wrote {args.out}/check.mp4, pose.npz, shuttle.npz, overlay.json ({(args.out / 'overlay.json').stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
