"""Analysis of one rally (milestone 4): the near-side player's pose, the shuttle, and who hit it when.

Ported from the spike (spike/pose_spike.py, spike/shuttle_hits.py), which was tuned against hand
labels on two London 2012 rallies. Positions are worked out in pixels of the analysed video and
saved as fractions of the frame, like the court calibration.

Court metres: x across the court (positive to the near player's right), y from the net (0)
towards the near baseline (+6.7). The calibration's homography maps frame fractions to these.
"""
from __future__ import annotations

import io
import json
import math
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

PIPELINE_VERSION = "1"

# COCO-WholeBody keypoints 0-16 are the body, 17-22 the feet.
N_KP = 23
FOOT_IDX = [15, 16, 17, 18, 19, 20, 21, 22]
KP_MIN = 0.3
# Left/right pairs, checked for swaps in two groups: arms, and legs and feet.
UPPER_PAIRS = [(5, 6), (7, 8), (9, 10)]
LOWER_PAIRS = [(11, 12), (13, 14), (15, 16), (17, 20), (18, 21), (19, 22)]

# Hit thresholds in pixels were tuned on 1008-pixel-high footage; they scale with frame height.
TUNED_HEIGHT = 1008

POSE_MODE = "lightweight"  # YOLOX-tiny + RTMW-m: matched the larger model in the spike at 3× the speed


# --- Models --------------------------------------------------------------------------------------

class PoseModel:
    """Person boxes and whole-body keypoints for a frame (frames in, keypoints out), so the model or
    device can change without touching the tracking."""

    def __init__(self, device: str = "cpu"):
        from rtmlib import Wholebody

        self._model = Wholebody(mode=POSE_MODE, backend="onnxruntime", device=device)
        self.providers = self._model.pose_model.session.get_providers()

    def detect(self, frame: np.ndarray) -> np.ndarray:
        return np.asarray(self._model.det_model(frame)).reshape(-1, 4)

    def keypoints(self, frame: np.ndarray, boxes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if len(boxes) == 0:
            return np.zeros((0, N_KP, 2), np.float32), np.zeros((0, N_KP), np.float32)
        kps, scores = self._model.pose_model(frame, bboxes=boxes)
        return np.asarray(kps)[:, :N_KP], np.asarray(scores)[:, :N_KP]


# --- Court and shirt ---------------------------------------------------------------------------

def to_court(H: np.ndarray, x: float, y: float, fw: int, fh: int) -> tuple[float, float]:
    p = H @ np.array([x / fw, y / fh, 1.0])
    return float(p[0] / p[2]), float(p[1] / p[2])


def lab(hex_colour: str) -> np.ndarray:
    bgr = np.uint8([[[int(hex_colour[i : i + 2], 16) for i in (5, 3, 1)]]])
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)[0, 0].astype(np.float32)


def shirt_distance(frame: np.ndarray, box, shirt: np.ndarray) -> float:
    """CIELAB distance from the middle of a person's torso to the shirt colour picked in review."""
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    crop = frame[int(y1 + 0.2 * h) : int(y1 + 0.5 * h), int(x1 + 0.25 * w) : int(x2 - 0.25 * w)]
    if crop.size == 0:
        return 100.0
    return float(np.linalg.norm(np.median(cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).reshape(-1, 3), 0) - shirt))


# --- Picking the players (from pose_spike.py) -----------------------------------------------------

def candidate_boxes(boxes, fw, fh, prev_center, H, keep=2):
    """The few detections that could be the near-side player, so pose runs on them only."""
    scored = []
    for b in boxes:
        x1, y1, x2, y2 = b[:4]
        if y2 - y1 < 0.08 * fh:
            continue
        cx, cy = to_court(H, (x1 + x2) / 2, y2, fw, fh)
        if not (-3.6 <= cx <= 3.6 and -0.3 <= cy <= 8.2):
            continue
        score = y2 / fh
        if prev_center is not None:
            score -= 2.0 * np.hypot((x1 + x2) / 2 - prev_center[0], (y1 + y2) / 2 - prev_center[1]) / fw
        scored.append((score, b[:4]))
    scored.sort(key=lambda s: -s[0])
    return np.array([b for _, b in scored[:keep]], np.float32).reshape(-1, 4)


def far_player_box(boxes, fw, fh, H, prev_far):
    """The opponent's box on the far half. Used only to tell who hit the shuttle."""
    best, best_score = None, -1e9
    for b in boxes:
        x1, y1, x2, y2 = map(float, b[:4])
        if y2 - y1 < 0.04 * fh:
            continue
        cx, cy = to_court(H, (x1 + x2) / 2, y2, fw, fh)
        if not (-3.6 <= cx <= 3.6 and -8.2 <= cy <= 0.3):
            continue
        score = (y2 - y1) / fh
        if prev_far is not None:
            score -= np.hypot((x1 + x2) / 2 - (prev_far[0] + prev_far[2]) / 2, y2 - prev_far[3]) / fh
        if score > best_score:
            best, best_score = np.array([x1, y1, x2, y2], np.float32), score
    return best


def pick_near_player(kps, scores, fw, fh, prev_center, H, shirt_d=None):
    """(index, centre, height) of the near-side player among the posed candidates, or None.
    shirt_d: each candidate's shirt-colour distance, a small tie-breaker when two people qualify."""
    best, best_score = None, -1e9
    for i in range(len(kps)):
        k, s = kps[i], scores[i]
        vis = s > KP_MIN
        if vis.sum() < 8:
            continue
        pts = k[vis]
        (x0, y0), (x1, y1) = pts.min(0), pts.max(0)
        h = y1 - y0
        if h < 0.08 * fh:
            continue
        fvis = s[FOOT_IDX] > KP_MIN
        if not fvis.any():
            continue
        foot = k[FOOT_IDX][fvis].mean(0)
        cx, cy = to_court(H, foot[0], foot[1], fw, fh)
        if not (-3.6 <= cx <= 3.6 and -0.3 <= cy <= 8.2):
            continue  # off court or on the far half
        centre = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
        score = foot[1] / fh  # nearer the camera = lower in frame
        if prev_center is not None:
            score -= 2.0 * np.hypot(*(centre - prev_center)) / fw
        elif not (0.15 * fw < centre[0] < 0.85 * fw):
            score -= 0.5  # line judges and coaches sit at the edges
        if shirt_d is not None:
            score -= 0.2 * min(1.0, shirt_d[i] / 60.0)
        if score > best_score:
            best, best_score = (i, centre, h), score
    return best


# --- Cleaning up the pose ------------------------------------------------------------------------

def fix_left_right(kp: np.ndarray, conf: np.ndarray, heights: np.ndarray) -> int:
    """Pose models sometimes flip a player's left and right limbs for a frame or two. A real change
    is gradual, so when swapping a frame's arms (or legs) puts them much closer to where they just
    were, swap them back. Works in place; returns how many swaps it made."""
    swaps = 0
    last = np.full((N_KP, 2), np.nan, np.float32)
    for t in range(len(kp)):
        if not np.isfinite(heights[t]):
            continue
        for pairs in (UPPER_PAIRS, LOWER_PAIRS):
            L = [a for a, _ in pairs]
            R = [b for _, b in pairs]
            ok = (conf[t, L] > KP_MIN) & (conf[t, R] > KP_MIN) & np.isfinite(last[L]).all(1) & np.isfinite(last[R]).all(1)
            if ok.sum() < 2:
                continue
            Lk, Rk = np.array(L)[ok], np.array(R)[ok]
            keep = np.linalg.norm(kp[t, Lk] - last[Lk], axis=1).sum() + np.linalg.norm(kp[t, Rk] - last[Rk], axis=1).sum()
            swap = np.linalg.norm(kp[t, Lk] - last[Rk], axis=1).sum() + np.linalg.norm(kp[t, Rk] - last[Lk], axis=1).sum()
            if swap < 0.5 * keep and keep > 0.1 * heights[t] * ok.sum():
                kp[t, L + R] = kp[t, R + L]
                conf[t, L + R] = conf[t, R + L]
                swaps += 1
        good = conf[t] > KP_MIN
        last[good] = kp[t, good]
    return swaps


def one_euro(kp: np.ndarray, conf: np.ndarray, fps: float, scale: float, min_cutoff=1.0, beta=5.0, d_cutoff=1.0) -> np.ndarray:
    """One-Euro filter over time for each keypoint: steady when still, little lag when moving fast.
    scale (frame height in pixels) makes beta independent of resolution. Low-confidence points are
    left as they are and restart the filter."""
    out = kp.copy()
    dt = 1.0 / fps
    alpha = lambda cutoff: 1.0 / (1.0 + 1.0 / (2 * math.pi * cutoff * dt))  # noqa: E731
    x_prev = np.full((N_KP, 2), np.nan, np.float32)
    dx_prev = np.zeros((N_KP, 2), np.float32)
    for t in range(len(kp)):
        x = kp[t]
        ok = (conf[t] > KP_MIN) & np.isfinite(x).all(1)
        fresh = ok & ~np.isfinite(x_prev).all(1)
        dx = np.where(fresh[:, None], 0, (x - x_prev) / dt)
        dx_hat = alpha(d_cutoff) * dx + (1 - alpha(d_cutoff)) * dx_prev
        a = alpha(min_cutoff + beta * np.linalg.norm(dx_hat, axis=1) / scale)[:, None]
        x_hat = np.where(fresh[:, None], x, a * x + (1 - a) * x_prev)
        out[t] = np.where(ok[:, None], x_hat, x)
        x_prev = np.where(ok[:, None], x_hat, np.nan).astype(np.float32)
        dx_prev = np.where(ok[:, None], dx_hat, 0).astype(np.float32)
    return out


# --- Shuttle and hits (from shuttle_hits.py) -----------------------------------------------------

def dedupe_and_fill(xy: np.ndarray, max_gap: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """Screen recordings repeat frames, so an exact repeat counts as missing. Gaps of up to max_gap
    frames are then filled by straight-line interpolation; longer gaps stay empty.
    Returns (filled positions, which frames were filled)."""
    out = xy.copy()
    for t in range(1, len(out)):
        if np.isfinite(xy[t]).all() and np.array_equal(xy[t], xy[t - 1]):
            out[t] = np.nan
    seen = np.isfinite(out).all(1)
    filled = np.zeros(len(out), bool)
    idx = np.flatnonzero(seen)
    for a, b in zip(idx, idx[1:]):
        if 1 < b - a <= max_gap + 1:
            for t in range(a + 1, b):
                out[t] = out[a] + (out[b] - out[a]) * (t - a) / (b - a)
                filled[t] = True
    return out, filled


def detect_hits(xy: np.ndarray, fps: float, px: float, min_gap_s=0.3, rise_px=35, turn_deg=75) -> list[tuple[int, float]]:
    """Each shot is an arc on screen, so a hit is the bottom of an arc (a local maximum of image y
    with a clear rise either side). Flat drives barely arc, so a sharp turn also counts.
    px: pixel scale relative to the tuned footage. Returns (frame index, strength) pairs."""
    raw_y = xy[:, 1]
    n = len(raw_y)
    y = np.full(n, np.nan)
    for t in range(n):  # centred 3-frame mean, ignoring gaps
        w = raw_y[max(0, t - 1) : t + 2]
        if np.isfinite(w).any():
            y[t] = np.nanmean(w)
    rise_px *= px
    k, win = max(2, round(0.12 * fps)), round(0.6 * fps)
    cands = []
    for t in range(k, n - k):
        if not np.isfinite(y[t - k : t + k + 1]).all() or y[t] < np.max(y[t - k : t + k + 1]):
            continue
        before, after = y[max(0, t - win) : t], y[t + 1 : t + 1 + win]
        if not (np.isfinite(before).any() and np.isfinite(after).any()):
            continue
        rise = min(y[t] - np.nanmin(before), y[t] - np.nanmin(after))
        if rise >= rise_px:
            cands.append((t, float(rise)))
    j = max(2, round(0.07 * fps))
    for t in range(j, n - j):
        a, b, c = xy[t - j], xy[t], xy[t + j]
        if not (np.isfinite(a).all() and np.isfinite(b).all() and np.isfinite(c).all()):
            continue
        v1, v2 = b - a, c - b
        n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
        if n1 > 6 * px and n2 > 6 * px and np.degrees(np.arccos(np.clip(np.dot(v1, v2) / (n1 * n2), -1, 1))) >= turn_deg:
            cands.append((t, 0.0))
    cands.sort()
    hits: list[tuple[int, float]] = []
    gap = round(min_gap_s * fps)
    for t, strength in cands:  # merge candidates closer than the gap, preferring arc bottoms
        if hits and t - hits[-1][0] < gap:
            if strength > hits[-1][1]:
                hits[-1] = (t, strength)
        else:
            hits.append((t, strength))
    return hits


def reach(box, p) -> float:
    """Distance from the shuttle to a player's reach (box widened sideways and upwards for the racket), in player heights."""
    x1, y1, x2, y2 = box
    h = max(1.0, y2 - y1)
    dx = max(x1 - 0.6 * h - p[0], 0, p[0] - (x2 + 0.6 * h))
    dy = max(y1 - 0.8 * h - p[1], 0, p[1] - y2)
    return float(np.hypot(dx, dy) / h)


def wrist_speed(kp, conf, seen, t, fps, px, half=4):
    """Peak wrist speed around frame t, in body heights per second (a racket swing). None if not seen."""
    best, any_seen = 0.0, False
    for s in range(max(1, t - half), min(len(kp), t + half + 1)):
        if not (seen[s - 1] and seen[s]):
            continue
        any_seen = True
        cb = conf[s]
        h = max(1.0, np.ptp(kp[s][cb > KP_MIN][:, 1])) if (cb > KP_MIN).sum() > 3 else 200.0 * px
        for w in (9, 10):
            if cb[w] > KP_MIN:
                best = max(best, float(np.linalg.norm(kp[s, w] - kp[s - 1, w]) * fps / h))
    return best if any_seen else None


def find_hits(near_kp, near_conf, near_seen, far_box, far_kp, far_conf, xy, fps, px, p_alternate=0.9, swing_ref=3.0):
    """Detects hits, then labels the hitter with a two-state Viterbi pass: rallies almost always
    alternate, and each hit carries evidence (whose racket arm swung harder; who the shuttle was
    nearer to). Returns dicts with index (into the rally), hitter and confidence."""
    far_seen = np.isfinite(far_box).all(1)
    hits = []
    for t, _ in detect_hits(xy, fps, px):
        p = xy[t]
        near = far = None
        if near_seen[t]:
            pts = near_kp[t][near_conf[t] > KP_MIN]
            if len(pts) > 3:
                near = reach((*pts.min(0), *pts.max(0)), p)
        if far_seen[t]:
            far = reach(far_box[t], p)
        swing = wrist_speed(near_kp, near_conf, near_seen, t, fps, px) or 0.0
        swing_far = wrist_speed(far_kp, far_conf, far_seen, t, fps, px)
        if swing_far is None:
            evidence = 2.5 * np.tanh((swing - swing_ref) / 1.5)
        else:
            evidence = 2.5 * np.tanh((swing - swing_far) / 2.0)
        if near is not None and far is not None:
            evidence += 1.0 * np.clip(far - near, -1, 1)
        hits.append({"index": t, "evidence": float(evidence)})
    if not hits:
        return hits
    lp_alt, lp_same = np.log(p_alternate), np.log(1 - p_alternate)
    emit = lambda h, st: -np.logaddexp(0, -h["evidence"]) if st == 0 else -np.logaddexp(0, h["evidence"])  # noqa: E731
    score = [emit(hits[0], 0), emit(hits[0], 1)]
    back = []
    for h in hits[1:]:
        new, ptr = [], []
        for st in (0, 1):
            cands = [score[prev] + (lp_same if prev == st else lp_alt) for prev in (0, 1)]
            k = int(np.argmax(cands))
            new.append(cands[k] + emit(h, st))
            ptr.append(k)
        score, back = new, back + [ptr]
    st = int(np.argmax(score))
    states = [st]
    for ptr in reversed(back):
        st = ptr[st]
        states.append(st)
    states.reverse()
    for h, st in zip(hits, states):
        h["hitter"] = "player" if st == 0 else "opponent"
        p_near = 1 / (1 + math.exp(-h["evidence"]))
        h["confidence"] = round(p_near if st == 0 else 1 - p_near, 3)
    return hits


# --- One rally -----------------------------------------------------------------------------------

@dataclass
class Rally:
    start_frame: int
    fps: float
    width: int
    height: int
    kp_raw: np.ndarray  # (T, 23, 2) pixels, after the left/right fix; NaN where the player wasn't found
    kp: np.ndarray  # the same, smoothed
    conf: np.ndarray  # (T, 23)
    far_box: np.ndarray  # (T, 4) pixels, NaN where the opponent wasn't found
    far_kp: np.ndarray
    far_conf: np.ndarray
    shuttle_raw: np.ndarray  # (T, 2) pixels from TrackNetV3, NaN where not seen
    shuttle: np.ndarray  # after removing repeats and filling short gaps
    shuttle_score: np.ndarray
    shuttle_filled: np.ndarray  # filled by InpaintNet or interpolation rather than seen
    hits: list[dict]
    stats: dict = field(default_factory=dict)

    @property
    def found(self) -> np.ndarray:
        return np.isfinite(self.kp_raw).all((1, 2))


def read_frames(path: str, start: int, end: int):
    """Frames start..end inclusive of the playback copy, as BGR arrays."""
    cap = cv2.VideoCapture(path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    for _ in range(start, end + 1):
        ok, frame = cap.read()
        if not ok:
            break
        yield frame
    cap.release()


def analyse_rally(path: str, start: int, end: int, fps: float, H: np.ndarray, shirt_hex: str | None,
                  pose: PoseModel, tracker, progress=None) -> Rally:
    """Pose every frame of the rally, then track the shuttle and find the hits.
    progress(fraction) is called now and then with how far through the rally it is."""
    from tracknet import shrink

    shirt = lab(shirt_hex) if shirt_hex else None
    T = end - start + 1
    kp = np.full((T, N_KP, 2), np.nan, np.float32)
    conf = np.zeros((T, N_KP), np.float32)
    heights = np.full(T, np.nan, np.float32)
    far_box = np.full((T, 4), np.nan, np.float32)
    far_kp = np.full((T, N_KP, 2), np.nan, np.float32)
    far_conf = np.zeros((T, N_KP), np.float32)
    small = []
    prev_center = prev_far = None
    fw = fh = 0
    t0 = time.time()
    n = 0
    for t, frame in enumerate(read_frames(path, start, end)):
        n = t + 1
        fh, fw = frame.shape[:2]
        small.append(shrink(frame))
        dets = pose.detect(frame)
        fb = far_player_box(dets, fw, fh, H, prev_far)
        prev_far = fb
        boxes = candidate_boxes(dets, fw, fh, prev_center, H)
        if fb is not None:  # pose the opponent too, in the same call, to see their racket swing
            boxes = np.vstack([boxes, fb[None]])
        kps, scs = pose.keypoints(frame, boxes)
        if fb is not None and len(kps):
            far_box[t], far_kp[t], far_conf[t] = fb, kps[-1], scs[-1]
            kps, scs, boxes = kps[:-1], scs[:-1], boxes[:-1]
        shirt_d = [shirt_distance(frame, b, shirt) for b in boxes] if shirt is not None and len(kps) > 1 else None
        pick = pick_near_player(kps, scs, fw, fh, prev_center, H, shirt_d) if len(kps) else None
        if pick:
            i, prev_center, h = pick
            kp[t], conf[t], heights[t] = kps[i], scs[i], h
        elif t and np.isnan(heights[max(0, t - int(fps)) : t]).all():
            prev_center = None  # lost for a second: stop expecting them where they were
        if progress and t % 30 == 0:
            progress(0.7 * t / T)
    pose_s = time.time() - t0
    T = n
    kp, conf, heights = kp[:T], conf[:T], heights[:T]
    far_box, far_kp, far_conf = far_box[:T], far_kp[:T], far_conf[:T]

    swaps = fix_left_right(kp, conf, heights)
    smooth = one_euro(kp, conf, fps, scale=fh)

    t1 = time.time()
    sh = tracker.track(np.stack(small), fw, fh)
    del small
    shuttle_s = time.time() - t1
    raw = np.stack([sh["x"], sh["y"]], 1)
    xy, interpolated = dedupe_and_fill(raw)
    px = fh / TUNED_HEIGHT
    found = np.isfinite(kp).all((1, 2))
    hits = find_hits(kp, conf, found, far_box, far_kp, far_conf, xy, fps, px)
    if progress:
        progress(1.0)

    stats = {
        "frames": T,
        "player_found_pct": round(100 * float(found.mean()), 1) if T else 0,
        "opponent_found_pct": round(100 * float(np.isfinite(far_box).all(1).mean()), 1) if T else 0,
        "shuttle_seen_pct": round(100 * float(np.isfinite(raw).all(1).mean()), 1) if T else 0,
        "left_right_swaps": swaps,
        "hits": len(hits),
        "player_hits": sum(h["hitter"] == "player" for h in hits),
        "pose_fps": round(T / pose_s, 1) if pose_s else None,
        "shuttle_s": round(shuttle_s, 1),
    }
    return Rally(start, fps, fw, fh, kp, smooth, conf, far_box, far_kp, far_conf, raw, xy, sh["score"],
                 sh["inpainted"] | interpolated, hits, stats)


# --- Files ---------------------------------------------------------------------------------------

def _frac(a: np.ndarray, w: int, h: int) -> np.ndarray:
    return (a / np.array([w, h], np.float32)).astype(np.float16)


def pose_npz(r: Rally, versions: dict) -> bytes:
    """pose/{rally_id}.npz: per-frame keypoints as frame fractions (float16), raw and smoothed."""
    w, h = r.width, r.height
    buf = io.BytesIO()
    np.savez_compressed(
        buf,
        frame=np.arange(r.start_frame, r.start_frame + len(r.kp), dtype=np.int32),
        kp=_frac(r.kp, w, h), kp_raw=_frac(r.kp_raw, w, h), conf=r.conf.astype(np.float16),
        far_box=_frac(r.far_box.reshape(-1, 2, 2), w, h).reshape(-1, 4),
        far_kp=_frac(r.far_kp, w, h), far_conf=r.far_conf.astype(np.float16),
        meta=np.array(json.dumps({"fps": r.fps, "width": w, "height": h, "keypoints": "COCO-WholeBody 0-22", **versions})),
    )
    return buf.getvalue()


def shuttle_npz(r: Rally, versions: dict) -> bytes:
    """shuttle/{rally_id}.npz: per-frame shuttle position as frame fractions (NaN where not found)."""
    buf = io.BytesIO()
    np.savez_compressed(
        buf,
        frame=np.arange(r.start_frame, r.start_frame + len(r.shuttle), dtype=np.int32),
        xy=_frac(r.shuttle, r.width, r.height), xy_raw=_frac(r.shuttle_raw, r.width, r.height),
        score=r.shuttle_score.astype(np.float16), filled=r.shuttle_filled,
        meta=np.array(json.dumps({"fps": r.fps, "width": r.width, "height": r.height, **versions})),
    )
    return buf.getvalue()


def overlay_json(r: Rally) -> bytes:
    """overlay/{rally_id}.json, what the viewer draws: per frame, the smoothed keypoints as
    [x, y, confidence, …] (x and y in 1/10000ths of the frame, confidence in hundredths) and the
    shuttle as [x, y], or null where nothing was found."""
    w, h = r.width, r.height
    kp_rows, sh_rows = [], []
    for t in range(len(r.kp)):
        if np.isfinite(r.kp[t]).all():
            row = np.concatenate([np.round(r.kp[t] / [w, h] * 10000), np.round(r.conf[t] * 100)[:, None]], 1)
            kp_rows.append(row.astype(int).ravel().tolist())
        else:
            kp_rows.append(None)
        s = r.shuttle[t]
        sh_rows.append(np.round(s / [w, h] * 10000).astype(int).tolist() if np.isfinite(s).all() else None)
    return json.dumps({"v": 1, "fps": r.fps, "start": r.start_frame, "kp": kp_rows, "shuttle": sh_rows},
                      separators=(",", ":")).encode()
