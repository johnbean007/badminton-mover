"""Shuttle spike: find hits in TrackNetV3 output and render them with the pose spike's skeleton.

Run pose_spike.py and TrackNetV3's predict.py first, then (from the spike/ folder):
    uv run shuttle_hits.py clips/lindan_lcw_r1.mp4

Reads  out/<clip>/pose.json and out/<clip>/shuttle/<clip>_ball.csv
Writes out/<clip>/combined.mp4   skeleton + shuttle trail + hit markers + timeline (contacts and hits)
       out/<clip>/hits.json      detected hits, each assigned to the near player or the opponent
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

from pose_spike import BONES, C_LEFT, C_MID, C_RIGHT, FOOT_IDX, KP_MIN, LEFT_KP, N_KP, RIGHT_KP, open_writer

C_SHUTTLE = (74, 216, 255)  # BGR yellow
C_HIT_NEAR, C_HIT_OPP = (74, 216, 255), (200, 200, 200)


def load_shuttle(csv: Path, n_frames: int) -> np.ndarray:
    d = pd.read_csv(csv)
    xy = np.full((n_frames, 2), np.nan, np.float32)
    for f, vis, x, y in d[["Frame", "Visibility", "X", "Y"]].itertuples(index=False):
        if vis and f < n_frames:
            xy[int(f)] = (x, y)
    return xy


def dedupe_and_fill(xy: np.ndarray) -> np.ndarray:
    """Screen recordings repeat frames. Treat an exact repeat as missing, then interpolate gaps of up to 5 frames."""
    out = xy.copy()
    for t in range(1, len(out)):
        if np.isfinite(xy[t]).all() and np.array_equal(xy[t], xy[t - 1]):
            out[t] = np.nan
    for axis in (0, 1):
        s = pd.Series(out[:, axis])
        out[:, axis] = s.interpolate(limit=5, limit_area="inside").to_numpy()
    return out


def detect_hits(xy: np.ndarray, fps: float, min_gap_s: float = 0.3, rise_px: float = 35, turn_deg: float = 110) -> list[tuple[int, float]]:
    """Hits from the broadcast view: each shot is an arc on screen, so a hit is the bottom of an arc
    (a local maximum of image y with a clear rise either side). Flat drives barely arc, so a sharp
    turn in direction also counts. Returns (frame, strength) pairs."""
    y = pd.Series(xy[:, 1]).rolling(3, center=True, min_periods=1).mean().to_numpy()
    n, k, win = len(y), max(2, round(0.12 * fps)), round(0.6 * fps)
    cands = []
    for t in range(k, n - k):
        if not np.isfinite(y[t]) or not np.isfinite(y[t - k:t + k + 1]).all():
            continue
        if y[t] < np.nanmax(y[t - k:t + k + 1]):
            continue
        before, after = y[max(0, t - win):t], y[t + 1:t + 1 + win]
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
        if n1 > 6 and n2 > 6 and np.degrees(np.arccos(np.clip(np.dot(v1, v2) / (n1 * n2), -1, 1))) >= turn_deg:
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


def reach(box, p):
    """Distance from the shuttle to a player's reach zone (box widened sideways and upwards for the racket), in player heights."""
    x1, y1, x2, y2 = box
    h = max(1.0, y2 - y1)
    dx = max(x1 - 0.6 * h - p[0], 0, p[0] - (x2 + 0.6 * h))
    dy = max(y1 - 0.8 * h - p[1], 0, p[1] - y2)
    return float(np.hypot(dx, dy) / h)


def wrist_speed(frames, t, fps, half=4, who="near"):
    """Peak wrist speed of a player around frame t, in body heights per second (a racket swing). None if not seen."""
    kk, ck = ("kp", "conf") if who == "near" else ("far_kp", "far_conf")
    best, seen = 0.0, False
    for s in range(max(1, t - half), min(len(frames), t + half + 1)):
        a, b = frames[s - 1], frames[s]
        if not (a.get(kk) and b.get(kk)):
            continue
        seen = True
        ka, kb, cb = np.array(a[kk]), np.array(b[kk]), np.array(b[ck])
        h = max(1.0, np.ptp(kb[cb > KP_MIN][:, 1])) if (cb > KP_MIN).sum() > 3 else 200.0
        for w in (9, 10):
            if cb[w] > KP_MIN:
                best = max(best, float(np.linalg.norm(kb[w] - ka[w]) * fps / h))
    return best if seen else None


def find_hits(frames, xy, fps, f_first, rise_px=35, turn_deg=75, p_alternate=0.9, swing_ref=3.0):
    """Detect hits, then label the hitter with a two-state Viterbi pass: rallies almost always alternate,
    and each hit carries evidence (shuttle within the near player's reach, a wrist swing, or within the far player's reach)."""
    hits = []
    for t, strength in detect_hits(xy, fps, rise_px=rise_px, turn_deg=turn_deg):
        fr, p = frames[t], xy[t]
        near = far = None
        if fr["kp"]:
            kp, cf = np.array(fr["kp"]), np.array(fr["conf"])
            pts = kp[cf > KP_MIN]
            if len(pts) > 3:
                near = reach((*pts.min(0), *pts.max(0)), p)
        if fr.get("far_box"):
            far = reach(fr["far_box"], p)
        swing = wrist_speed(frames, t, fps) or 0.0
        swing_far = wrist_speed(frames, t, fps, who="far")
        # log-odds that the near player hit it: whose racket arm swung harder; reach only counts when both players are seen
        if swing_far is None:
            evidence = 2.5 * np.tanh((swing - swing_ref) / 1.5)
        else:
            evidence = 2.5 * np.tanh((swing - swing_far) / 2.0)
        if near is not None and far is not None:
            evidence += 1.0 * np.clip(far - near, -1, 1)
        hits.append({"frame": f_first + t, "t": round((f_first + t) / fps, 3),
                     "near_reach": None if near is None else round(near, 2), "far_reach": None if far is None else round(far, 2),
                     "swing": round(swing, 2), "swing_far": None if swing_far is None else round(swing_far, 2), "evidence": round(float(evidence), 2)})
    if not hits:
        return hits
    # Viterbi over states 0 = near player, 1 = opponent
    lp_alt, lp_same = np.log(p_alternate), np.log(1 - p_alternate)
    emit = lambda h, st: -np.logaddexp(0, -h["evidence"]) if st == 0 else -np.logaddexp(0, h["evidence"])
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
        h["who"] = "near player" if st == 0 else "opponent"
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip", type=Path)
    ap.add_argument("--hand", choices=["R", "L"], default=None, help="defaults to the hand used in pose.json zones")
    args = ap.parse_args()

    out_dir = Path("out") / args.clip.stem
    pose = json.loads((out_dir / "pose.json").read_text())
    frames, contacts, f_first = pose["frames"], pose["contacts"], pose["start_frame"]
    fps = pose["summary"]["fps"]
    T = len(frames)
    raw = load_shuttle(out_dir / "shuttle" / f"{args.clip.stem}_ball.csv", f_first + T)[f_first:]
    xy = dedupe_and_fill(raw)
    hits = find_hits(frames, xy, fps, f_first)
    (out_dir / "hits.json").write_text(json.dumps(hits, indent=1))

    # Render
    cap = cv2.VideoCapture(str(args.clip))
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    writer = open_writer(out_dir / "combined.mp4", W, H + 96, fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, f_first)
    trail = round(0.4 * fps)
    f_last = f_first + T - 1
    span = max(1, f_last - f_first)
    X = lambda f: int(10 + (f - f_first) / span * (W - 20))
    for t in range(T):
        ok, img = cap.read()
        if not ok:
            break
        f_abs = f_first + t
        fr = frames[t]
        down = {n: any(c["foot"] == n and c["f0"] <= f_abs <= c["f1"] for c in contacts) for n in ("L", "R")}
        if fr["kp"]:
            kp, cf = np.array(fr["kp"]), np.array(fr["conf"])
            for a, b in BONES:
                if cf[a] > KP_MIN and cf[b] > KP_MIN:
                    col = C_LEFT if (a in LEFT_KP and b in LEFT_KP) else C_RIGHT if (a in RIGHT_KP and b in RIGHT_KP) else C_MID
                    cv2.line(img, tuple(map(int, kp[a])), tuple(map(int, kp[b])), col, 2, cv2.LINE_AA)
            for i in FOOT_IDX:
                if cf[i] > KP_MIN:
                    col = C_LEFT if i in LEFT_KP else C_RIGHT
                    p = tuple(map(int, kp[i]))
                    is_down = down["L"] if i in LEFT_KP else down["R"]
                    cv2.circle(img, p, 6, col, -1 if is_down else 2, cv2.LINE_AA)
        # shuttle trail and ring
        pts = [(s, xy[s]) for s in range(max(0, t - trail), t + 1) if np.isfinite(xy[s]).all()]
        for (s0, p0), (s1, p1) in zip(pts, pts[1:]):
            a = (s1 - (t - trail)) / trail
            cv2.line(img, tuple(map(int, p0)), tuple(map(int, p1)), C_SHUTTLE, max(1, int(1 + 3 * a)), cv2.LINE_AA)
        if np.isfinite(xy[t]).all():
            cv2.circle(img, tuple(map(int, xy[t])), 9, C_SHUTTLE, 2, cv2.LINE_AA)
        recent = [h for h in hits if 0 <= f_abs - h["frame"] < round(0.5 * fps)]
        for h in recent:
            p = xy[h["frame"] - f_first]
            if np.isfinite(p).all():
                col = C_HIT_NEAR if h["who"] == "near player" else C_HIT_OPP
                cv2.circle(img, tuple(map(int, p)), 22, col, 3, cv2.LINE_AA)
                cv2.putText(img, "HIT" if h["who"] == "near player" else "opp hit", (int(p[0]) + 26, int(p[1]) + 6), cv2.FONT_HERSHEY_SIMPLEX, .7, col, 2, cv2.LINE_AA)
        cv2.rectangle(img, (16, 16), (300, 92), (20, 20, 20), -1)
        cv2.putText(img, f"{f_abs / fps:6.2f} s  frame {f_abs}", (28, 42), cv2.FONT_HERSHEY_SIMPLEX, .6, (240, 240, 240), 1, cv2.LINE_AA)
        for j, (n, col) in enumerate((("L", C_LEFT), ("R", C_RIGHT))):
            cv2.putText(img, f"{n} {'DOWN' if down[n] else 'air'}", (28 + j * 130, 76), cv2.FONT_HERSHEY_SIMPLEX, .7, col, 2 if down[n] else 1, cv2.LINE_AA)
        # timeline: shuttle hits, left contacts, right contacts
        strip = np.full((96, W, 3), 24, np.uint8)
        cv2.putText(strip, "shuttle", (12, 22), cv2.FONT_HERSHEY_SIMPLEX, .45, (170, 170, 170), 1)
        for h in hits:
            x = X(h["frame"])
            col = C_HIT_NEAR if h["who"] == "near player" else C_HIT_OPP
            pts_d = np.array([[x, 10], [x + 7, 18], [x, 26], [x - 7, 18]], np.int32)
            cv2.fillPoly(strip, [pts_d], col) if h["who"] == "near player" else cv2.polylines(strip, [pts_d], True, col, 2)
        for c in contacts:
            y = 38 if c["foot"] == "L" else 64
            col = C_LEFT if c["foot"] == "L" else C_RIGHT
            cv2.rectangle(strip, (X(c["f0"]), y), (max(X(c["f0"]) + 2, X(c["f1"])), y + 18), col, -1)
        cv2.line(strip, (X(f_abs), 4), (X(f_abs), 92), (80, 80, 240), 2)
        writer.stdin.write(np.vstack([img, strip]).tobytes())
    writer.stdin.close()
    writer.wait()
    cap.release()

    near = [h for h in hits if h["who"] == "near player"]
    vis = np.isfinite(raw).all(1).mean() * 100
    print(f"Shuttle found in {vis:.1f}% of frames")
    print(f"Hits: {len(hits)} ({len(near)} near player, {len(hits) - len(near)} opponent)")
    for h in hits:
        print(f"  {h['t']:6.2f} s  {h['who']:12s} evidence {h['evidence']:+.2f}")
    print(f"Wrote {out_dir}/combined.mp4 and hits.json")


if __name__ == "__main__":
    main()
