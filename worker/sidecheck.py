"""Near-side check: in each segment, is the tracked player on the near half of the court?

Samples a few frames per segment, finds the people standing on the court (YOLOX person detector,
court position from the calibration), and compares each player's shirt with the colour the member
picked. Phase 1 tracks the near-side player only, so segments where the chosen player is on the far
side are skipped.
"""
from __future__ import annotations

import cv2
import numpy as np

FRAMES_PER_SEGMENT = 5
MARGIN = 12.0  # CIELAB units the closer shirt must win by for a frame to count as a vote
MIN_VOTES = 3


def to_court(H: np.ndarray, x: float, y: float) -> tuple[float, float]:
    p = H @ np.array([x, y, 1.0])
    return float(p[0] / p[2]), float(p[1] / p[2])


def lab(hex_colour: str) -> np.ndarray:
    rgb = np.uint8([[[int(hex_colour[i : i + 2], 16) for i in (5, 3, 1)]]])  # as BGR
    return cv2.cvtColor(rgb, cv2.COLOR_BGR2LAB)[0, 0].astype(np.float32)


def torso_colour(frame: np.ndarray, box) -> np.ndarray:
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    crop = frame[int(y1 + 0.2 * h) : int(y1 + 0.5 * h), int(x1 + 0.25 * w) : int(x2 - 0.25 * w)]
    if crop.size == 0:
        return np.full(3, np.nan, np.float32)
    return np.median(cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).reshape(-1, 3), axis=0).astype(np.float32)


def players(frame: np.ndarray, boxes, H: np.ndarray) -> dict[str, tuple]:
    """The biggest person standing on each half of the court: {'near': box, 'far': box}."""
    fh, fw = frame.shape[:2]
    best: dict[str, tuple[float, tuple]] = {}
    for b in boxes:
        x1, y1, x2, y2 = (float(v) for v in b[:4])
        if y2 - y1 < 0.05 * fh:
            continue
        cx, cy = to_court(H, (x1 + x2) / 2 / fw, y2 / fh)
        if not (-4.0 <= cx <= 4.0 and -8.5 <= cy <= 8.5):
            continue  # umpires, line judges, coaches
        side = "near" if cy > 0.3 else "far" if cy < -0.3 else None
        if side and (side not in best or y2 - y1 > best[side][0]):
            best[side] = (y2 - y1, (x1, y1, x2, y2))
    return {k: v[1] for k, v in best.items()}


def check_segment(frames: list[np.ndarray], detect, H: np.ndarray, shirt: np.ndarray) -> tuple[str, list[str]]:
    votes = []
    for frame in frames:
        found = players(frame, detect(frame), H)
        if "near" not in found or "far" not in found:
            votes.append("missing")
            continue
        d_near = float(np.linalg.norm(torso_colour(frame, found["near"]) - shirt))
        d_far = float(np.linalg.norm(torso_colour(frame, found["far"]) - shirt))
        votes.append("near" if d_near + MARGIN < d_far else "far" if d_far + MARGIN < d_near else "unsure")
    near, far = votes.count("near"), votes.count("far")
    if near >= MIN_VOTES and near > 2 * far:
        return "near", votes
    if far >= MIN_VOTES and far > 2 * near:
        return "far", votes
    return "unclear", votes


def sample_frames(path: str, start: int, end: int, n: int = FRAMES_PER_SEGMENT) -> list[np.ndarray]:
    cap = cv2.VideoCapture(path)
    out = []
    for f in np.linspace(start, end, n + 2)[1:-1].astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(f))
        ok, img = cap.read()
        if ok:
            out.append(img)
    cap.release()
    return out
