"""Does a court calibration still match the camera view? Broadcast cameras zoom and pan between
rallies, and a calibration clicked on one frame then puts every later court position in the wrong
place. The check projects the calibrated court lines into a frame and counts how many of the
sampled points land on white line pixels: about 0.95 when it fits, far lower when it doesn't.
"""
from __future__ import annotations

import cv2
import numpy as np

FIT_OK = 0.75  # below this the review page and viewer warn (same threshold in web/src/lib/court.ts)

# Court lines in metres (x across, y from the net): outer and singles sidelines, baselines, short and
# long service lines, centre lines.
_D, _S, _L = 3.05, 2.59, 6.7
LINES = [
    ((-_D, _L), (_D, _L)), ((-_D, -_L), (_D, -_L)), ((-_D, -_L), (-_D, _L)), ((_D, -_L), (_D, _L)),
    ((-_S, -_L), (-_S, _L)), ((_S, -_L), (_S, _L)), ((-_D, 1.98), (_D, 1.98)), ((-_D, -1.98), (_D, -1.98)),
    ((-_D, 5.94), (_D, 5.94)), ((-_D, -5.94), (_D, -5.94)), ((0, 1.98), (0, _L)), ((0, -1.98), (0, -_L)),
]
SAMPLES_PER_LINE = 60


def line_fit(frame: np.ndarray, H: np.ndarray) -> float | None:
    """Share of sampled court-line points (H: frame fractions → court metres) that sit on white
    pixels, allowing a couple of pixels of slack. None if the court isn't in the frame at all."""
    fh, fw = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    white = ((hsv[:, :, 2] > 170) & (hsv[:, :, 1] < 70)).astype(np.uint8)
    white = cv2.dilate(white, np.ones((5, 5), np.uint8))
    inv = np.linalg.inv(H)
    inv = inv / (inv @ np.array([0.0, 0.0, 1.0]))[2]  # a homography's sign is arbitrary: make the court centre's w positive
    hit = n = 0
    for (x0, y0), (x1, y1) in LINES:
        for s in np.linspace(0, 1, SAMPLES_PER_LINE):
            p = inv @ np.array([x0 + (x1 - x0) * s, y0 + (y1 - y0) * s, 1.0])
            if p[2] <= 0:
                continue
            x, y = p[0] / p[2] * fw, p[1] / p[2] * fh
            if 0 <= x < fw and 0 <= y < fh:
                n += 1
                hit += int(white[int(y), int(x)])
    return hit / n if n >= 100 else None


def rally_fit(path: str, start: int, end: int, H: np.ndarray, samples: int = 3) -> float:
    """The best fit over a few frames spread through the rally (a player standing on a line, or a
    replay frame, can spoil one frame)."""
    cap = cv2.VideoCapture(path)
    scores = []
    for f in np.linspace(start, end, samples + 2)[1:-1].astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(f))
        ok, frame = cap.read()
        if ok:
            s = line_fit(frame, H)
            scores.append(0.0 if s is None else s)
    cap.release()
    return round(max(scores), 3) if scores else 0.0
