"""Foot contacts and court zones (milestone 5), worked out from a rally's stored pose file, so they
can be re-run after a rule change without tracking the pose again.

A contact is a run of frames where a foot's ground point (between heel and big toe) is still:
it lands when the foot stops moving and lifts off when it moves again. Speeds are measured in
player heights per second, so the same threshold works at any zoom and frame rate.
"""
from __future__ import annotations

import io
import json

import numpy as np

RULES_VERSION = "contacts-1"
KP_MIN = 0.3
LEFT = {"ankle": 15, "big_toe": 17, "heel": 19}
RIGHT = {"ankle": 16, "big_toe": 20, "heel": 22}

STILL = 0.5  # foot speed below this (player heights per second) counts as planted
MIN_CONTACT_S = 0.05  # shorter stillness is a foot passing through, not a plant
BRIDGE_S = 0.04  # a flicker of movement this short inside a plant doesn't end it

# Court metres (x across, positive to the player's right; y from the net towards the near baseline).
HALF_LENGTH = 6.7
SINGLES_HALF_WIDTH = 2.59
ROW_EDGES = (2.0, 4.6)  # front | mid | rear, metres from the net
COLUMN = 2 * SINGLES_HALF_WIDTH / 3  # equal thirds of the singles width
OUT_MARGIN = 0.1  # metres of slack for a foot on the line
AT_REST = 0.03  # player heights: a foot this close to the plant's spot is already (or still) on it


def zone_of(x: float, y: float, hand: str | None) -> tuple[str, bool]:
    """The zone a ground point falls in, and whether it's outside the singles court (then it takes
    the nearest zone). Forehand side = the player's right for right-handers, left for left-handers."""
    out = abs(x) > SINGLES_HALF_WIDTH + OUT_MARGIN or not (-OUT_MARGIN <= y <= HALF_LENGTH + OUT_MARGIN)
    col = 0 if x < -COLUMN / 2 else 2 if x > COLUMN / 2 else 1  # 0 = player's left
    row = 0 if y < ROW_EDGES[0] else 1 if y < ROW_EDGES[1] else 2
    left, right = ("FH", "BH") if hand == "L" else ("BH", "FH")
    names = [[f"Front {left}", "Front C", f"Front {right}"], [f"Mid {left}", "Base", f"Mid {right}"], [f"Rear {left}", "Rear C", f"Rear {right}"]]
    return names[row][col], out


def to_court(H: np.ndarray, pt) -> tuple[float, float]:
    p = H @ np.array([pt[0], pt[1], 1.0])
    return float(p[0] / p[2]), float(p[1] / p[2])


def player_heights(kp: np.ndarray, conf: np.ndarray) -> np.ndarray:
    """Per-frame player height in frame fractions (visible keypoints' vertical spread), steadied
    with a 0.5 s running median and filled where the player wasn't seen."""
    T = len(kp)
    h = np.full(T, np.nan)
    for t in range(T):
        good = conf[t] > KP_MIN
        if good.sum() > 3 and np.isfinite(kp[t][good]).all():
            h[t] = np.ptp(kp[t][good][:, 1])
    steady = h.copy()
    for t in range(T):
        w = h[max(0, t - 7) : t + 8]
        steady[t] = np.nanmedian(w) if np.isfinite(w).any() else np.nan
    fallback = np.nanmedian(h) if np.isfinite(h).any() else 0.3
    return np.where(np.isfinite(steady), steady, fallback)


def ground_point(kp: np.ndarray, conf: np.ndarray, foot: dict) -> tuple[np.ndarray, np.ndarray]:
    """Per-frame ground point of one foot (mean of heel and big toe, else the ankle) and its confidence."""
    T = len(kp)
    g = np.full((T, 2), np.nan)
    c = np.zeros(T)
    for t in range(T):
        good = [i for i in (foot["heel"], foot["big_toe"]) if conf[t, i] > KP_MIN]
        if good:
            g[t] = kp[t, good].mean(0)
            c[t] = conf[t, good].mean()
        elif conf[t, foot["ankle"]] > KP_MIN:
            g[t] = kp[t, foot["ankle"]]
            c[t] = 0.5 * conf[t, foot["ankle"]]
    return g, c


def foot_speed(g: np.ndarray, heights: np.ndarray, fps: float) -> np.ndarray:
    """Speed of the ground point in player heights per second, from the frames either side. Spanning
    two frames means a repeated frame (common in screen recordings) doesn't read as a stop."""
    T = len(g)
    v = np.full(T, np.nan)
    for t in range(T):
        a, b = max(0, t - 1), min(T - 1, t + 1)
        if b > a and np.isfinite(g[a]).all() and np.isfinite(g[b]).all():
            v[t] = np.linalg.norm(g[b] - g[a]) / (b - a) * fps / heights[t]
    return v


def detect(kp: np.ndarray, conf: np.ndarray, fps: float, start_frame: int, H: np.ndarray, hand: str | None) -> list[dict]:
    """Contacts for both feet, sorted by landing. kp: (T, 23, 2) smoothed keypoints as frame fractions."""
    heights = player_heights(kp, conf)
    min_len = max(2, round(MIN_CONTACT_S * fps))
    bridge = max(1, round(BRIDGE_S * fps))
    contacts = []
    for name, foot in (("L", LEFT), ("R", RIGHT)):
        g, c = ground_point(kp, conf, foot)
        still = np.nan_to_num(foot_speed(g, heights, fps), nan=99.0) < STILL
        t, T = 0, len(still)
        while t < T:
            if not still[t]:
                t += 1
                continue
            end = t
            while True:  # extend over still frames, bridging short flickers of movement
                nxt = end + 1
                while nxt < T and not still[nxt] and nxt - end <= bridge:
                    nxt += 1
                if nxt < T and still[nxt] and nxt - end <= bridge + 1:
                    end = nxt
                else:
                    break
            if end - t + 1 >= min_len:
                pt = np.nanmedian(g[t : end + 1], axis=0)
                # Speeds span the frames either side, which trims a frame off each end of a plant;
                # take back neighbours where the foot is already (or still) on the spot.
                first, last = t, end
                for _ in range(2):
                    if first > 0 and np.isfinite(g[first - 1]).all() and np.linalg.norm(g[first - 1] - pt) < AT_REST * heights[first - 1]:
                        first -= 1
                    if last < T - 1 and np.isfinite(g[last + 1]).all() and np.linalg.norm(g[last + 1] - pt) < AT_REST * heights[last + 1]:
                        last += 1
                t, end = first, last
                x, y = to_court(H, pt)
                zone, out = zone_of(x, y, hand)
                # Confident when the foot keypoints were clear and the plant lasted a while.
                confidence = float(np.clip(np.mean(c[t : end + 1]) * min(1.0, (end - t + 1) / (2 * min_len)), 0, 1))
                contacts.append({"foot": name, "start_frame": start_frame + t, "end_frame": start_frame + end,
                                 "court_x_m": round(x, 3), "court_y_m": round(y, 3), "zone": zone, "out_of_court": out,
                                 "confidence": round(confidence, 3), "image_xy": [round(float(pt[0]), 4), round(float(pt[1]), 4)]})
            t = end + 1
    contacts.sort(key=lambda k: (k["start_frame"], k["foot"]))
    return contacts


def load_pose(npz_bytes: bytes) -> dict:
    z = np.load(io.BytesIO(npz_bytes))
    return {"frame": z["frame"], "kp": z["kp"].astype(np.float64), "conf": z["conf"].astype(np.float64),
            "meta": json.loads(str(z["meta"]))}


def summary(contacts: list[dict], frames: int, fps: float) -> dict:
    durs = [(k["end_frame"] - k["start_frame"] + 1) / fps * 1000 for k in contacts]
    zones: dict[str, int] = {}
    for k in contacts:
        zones[k["zone"]] = zones.get(k["zone"], 0) + 1
    return {"contacts": len(contacts), "left": sum(k["foot"] == "L" for k in contacts),
            "per_second": round(len(contacts) / (frames / fps), 2) if frames else 0,
            "median_ms": round(float(np.median(durs))) if durs else None,
            "out_of_court": sum(k["out_of_court"] for k in contacts), "zones": zones}
