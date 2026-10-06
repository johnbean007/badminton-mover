"""Does a court calibration still match the camera view? Broadcast cameras zoom and pan between
rallies, and a calibration clicked on one frame then puts every later court position in the wrong
place. The check projects the calibrated court lines into a frame and counts how many of the
sampled points land on white line pixels: about 0.95 when it fits, far lower when it doesn't.
"""
from __future__ import annotations

import cv2
import numpy as np

FIT_OK = 0.75  # below this the review page and viewer warn (same threshold in web/src/lib/court.ts)
SNAP_BELOW = 0.9  # calibrations fitting worse than this get snapped to the visible lines
MAX_SNAP = 0.1  # frame fractions a corner may move when snapping; further means the click was for another view
COURT_CORNERS = np.float32([[-3.05, 6.7], [3.05, 6.7], [3.05, -6.7], [-3.05, -6.7]])

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


def sample_frames(path: str, start: int, end: int, samples: int = 3) -> list[np.ndarray]:
    cap = cv2.VideoCapture(path)
    frames = []
    for f in np.linspace(start, end, samples + 2)[1:-1].astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(f))
        ok, frame = cap.read()
        if ok:
            frames.append(frame)
    cap.release()
    return frames


def frames_fit(frames: list[np.ndarray], H: np.ndarray) -> float:
    """The best fit over a few frames (a player standing on a line, or a replay frame, can spoil one)."""
    scores = [line_fit(f, H) or 0.0 for f in frames]
    return round(max(scores), 3) if scores else 0.0


def rally_fit(path: str, start: int, end: int, H: np.ndarray, samples: int = 3) -> float:
    return frames_fit(sample_frames(path, start, end, samples), H)


# --- Snapping a rough calibration onto the visible lines -------------------------------------------

def homography_from_corners(corners) -> np.ndarray:
    return cv2.getPerspectiveTransform(np.float32(np.reshape(corners, (4, 2))), COURT_CORNERS).astype(float)


_LINE_POINTS = np.array([(x0 + (x1 - x0) * s, y0 + (y1 - y0) * s, 1.0) for (x0, y0), (x1, y1) in LINES
                         for s in np.linspace(0, 1, SAMPLES_PER_LINE)]).T


def _distance_cost(frames: list[np.ndarray], clip: float):
    """Mean distance (pixels, capped at `clip`) from the projected court lines to the nearest white
    pixel: smooth enough to optimise, unlike the hit-or-miss fit score."""
    fh, fw = frames[0].shape[:2]
    maps = []
    for img in frames:
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        white = ((hsv[:, :, 2] > 170) & (hsv[:, :, 1] < 70)).astype(np.uint8)
        maps.append(np.minimum(cv2.distanceTransform(1 - white, cv2.DIST_L2, 3), clip))

    def cost(c: np.ndarray) -> float:
        try:
            inv = np.linalg.inv(homography_from_corners(c))
        except np.linalg.LinAlgError:
            return 1e3
        inv = inv / (inv @ np.array([0.0, 0.0, 1.0]))[2]
        p = inv @ _LINE_POINTS
        x, y = p[0] / p[2] * fw, p[1] / p[2] * fh
        vis = (p[2] > 0) & (x >= 0) & (x < fw) & (y >= 0) & (y < fh)
        if vis.sum() < 300:
            return 1e3
        return float(np.mean([m[y[vis].astype(int), x[vis].astype(int)].mean() for m in maps]))

    return cost


def _nelder_mead(f, x0: np.ndarray, step: float, iters: int) -> np.ndarray:
    n = len(x0)
    simplex = [x0] + [x0 + np.eye(n)[i] * step for i in range(n)]
    vals = [f(x) for x in simplex]
    for _ in range(iters):
        order = np.argsort(vals)
        simplex, vals = [simplex[i] for i in order], [vals[i] for i in order]
        centre = np.mean(simplex[:-1], 0)
        xr = centre + (centre - simplex[-1])
        fr = f(xr)
        if fr < vals[0]:
            xe = centre + 2 * (centre - simplex[-1])
            fe = f(xe)
            simplex[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            simplex[-1], vals[-1] = xr, fr
        else:
            xc = centre + 0.5 * (simplex[-1] - centre)
            fc = f(xc)
            if fc < vals[-1]:
                simplex[-1], vals[-1] = xc, fc
            else:
                simplex = [simplex[0] + 0.5 * (s - simplex[0]) for s in simplex]
                vals = [f(s) for s in simplex]
    return simplex[int(np.argmin(vals))]


def snap(frames: list[np.ndarray], corners) -> tuple[np.ndarray, float] | None:
    """Moves a roughly clicked calibration (outer corners, frame fractions; they may be off-screen)
    onto the court lines visible in `frames`, coarse to fine. Returns (corners, fit) when that
    clearly helps and stays close to the clicks, else None."""
    start = np.asarray(corners, float).ravel()
    before = frames_fit(frames, homography_from_corners(start))
    best = start
    for clip, step, iters in ((40, 0.03, 1200), (20, 0.01, 1200), (8, 0.004, 1200), (8, 0.0015, 800)):
        best = _nelder_mead(_distance_cost(frames, clip), best, step, iters)
    after = frames_fit(frames, homography_from_corners(best))
    moved = np.abs(best - start).max()
    if after >= FIT_OK and after > before + 0.05 and moved <= MAX_SNAP:
        return best.reshape(4, 2), after
    return None
