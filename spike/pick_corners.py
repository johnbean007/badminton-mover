"""Click the four singles-court corners on a frame and save them next to the clip.

Usage (from the spike/ folder, in your own Terminal so the window can open):
    uv run pick_corners.py clips/final.mp4 [--at 5]

Click in this order: near-left, near-right, far-right, far-left (the singles sidelines,
the inner of the two side lines). Press r to start again, s to save, q to quit.
Saves clips/<clip name>.corners.txt, which pose_spike.py picks up automatically.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2

NAMES = ["near-left", "near-right", "far-right", "far-left"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip", type=Path)
    ap.add_argument("--at", type=float, default=1.0, help="seconds into the clip to grab the frame")
    args = ap.parse_args()

    cap = cv2.VideoCapture(str(args.clip))
    cap.set(cv2.CAP_PROP_POS_MSEC, args.at * 1000)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"Can't read a frame from {args.clip} at {args.at} s")

    pts: list[tuple[int, int]] = []

    def on_click(event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN and len(pts) < 4:
            pts.append((x, y))

    win = "Click the singles court corners"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(win, on_click)
    while True:
        img = frame.copy()
        for i, p in enumerate(pts):
            cv2.circle(img, p, 7, (77, 225, 255), -1)
            cv2.putText(img, NAMES[i], (p[0] + 10, p[1] - 10), cv2.FONT_HERSHEY_SIMPLEX, .7, (77, 225, 255), 2)
        if len(pts) > 1:
            for a, b in zip(pts, pts[1:] + ([pts[0]] if len(pts) == 4 else [])):
                cv2.line(img, a, b, (77, 225, 255), 2)
        msg = f"Click the {NAMES[len(pts)]} corner" if len(pts) < 4 else "s = save, r = redo, q = quit"
        cv2.rectangle(img, (10, 10), (520, 50), (20, 20, 20), -1)
        cv2.putText(img, msg, (20, 38), cv2.FONT_HERSHEY_SIMPLEX, .8, (255, 255, 255), 2)
        cv2.imshow(win, img)
        k = cv2.waitKey(30) & 0xFF
        if k == ord("r"):
            pts.clear()
        elif k == ord("q"):
            break
        elif k == ord("s") and len(pts) == 4:
            out = args.clip.with_suffix(".corners.txt")
            out.write_text(";".join(f"{x},{y}" for x, y in pts))
            print(f"Saved {out}")
            break
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
