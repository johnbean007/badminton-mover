"""Pre-scan: find camera cuts in a broadcast clip and propose rally segments.

Broadcast singles is filmed mostly from one high camera behind the near baseline. Rallies are
played on that camera; replays, close-ups and crowd shots use others. So: split the video into
shots at camera cuts, find the camera that most of the footage comes from, and propose each long
enough shot from it as a segment. The member then fixes the proposal in the review step.

Runs on the CFR playback copy in the worker, so frame numbers match what the viewer plays.

    .venv/bin/python prescan.py <video> [--sheet out.jpg]   # try it locally
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

import cv2
import numpy as np
from scenedetect import AdaptiveDetector, SceneManager, open_video

MIN_SEGMENT_S = 2.0  # shorter main-camera shots are usually the camera settling after a cut
# Mean colour difference (CIELAB units) between two shots' thumbnails below which they count as the
# same camera. On the London 2012 final, main-camera shots score 4–10 against each other (31 when the
# camera zooms in during a rally); close-ups, crowd shots and side-on replays score 41 or more.
SAME_CAMERA = 36.0


@dataclass
class Shot:
    start: int  # first frame
    end: int  # last frame, inclusive
    feature: np.ndarray
    main: bool = False
    similarity: float = 0.0

    @property
    def frames(self) -> int:
        return self.end - self.start + 1


def frame_index(tc) -> int:
    return tc.frame_num if hasattr(tc, "frame_num") else tc.get_frames()


def find_shots(path: str) -> tuple[list[tuple[int, int]], float, int]:
    video = open_video(path)
    manager = SceneManager()
    manager.add_detector(AdaptiveDetector(min_scene_len=8))
    manager.auto_downscale = True
    manager.detect_scenes(video)
    total = frame_index(video.position)
    cuts = manager.get_scene_list(start_in_scene=True)
    shots = [(frame_index(a), frame_index(b) - 1) for a, b in cuts] or [(0, total - 1)]
    return shots, float(video.frame_rate), total


def shot_feature(cap: cv2.VideoCapture, start: int, end: int) -> np.ndarray:
    """A tiny colour picture of the shot (CIELAB, averaged over three frames). Players running
    about barely change it; a different camera angle changes it a lot. The main camera's big
    green court and the surrounding colours are what it keys on."""
    acc = []
    for f in np.linspace(start, end, 5)[1:4].astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(f))
        ok, img = cap.read()
        if ok:
            small = cv2.resize(img, (32, 18), interpolation=cv2.INTER_AREA)
            acc.append(cv2.cvtColor(small, cv2.COLOR_BGR2LAB).astype(np.float32))
    return np.mean(acc, axis=0) if acc else np.full((18, 32, 3), np.nan, np.float32)


def classify(shots: list[Shot]) -> None:
    """Marks shots from the main camera: the angle that the most footage is filmed from."""
    if not shots:
        return
    feats = np.stack([s.feature for s in shots])
    dist = np.nan_to_num(np.linalg.norm(feats[:, None] - feats[None, :], axis=-1).mean(axis=(2, 3)), nan=1e9)
    weight = np.array([s.frames for s in shots], np.float32)
    # The reference shot is the one that the most footage looks like.
    ref = int(np.argmax((dist < SAME_CAMERA) @ weight))
    for i, s in enumerate(shots):
        s.similarity = float(dist[ref, i])
        s.main = s.similarity < SAME_CAMERA


def merge_main(shots: list[Shot]) -> list[tuple[int, int]]:
    """Back-to-back main-camera shots are one stretch of play (the detector sometimes cuts on a
    fast camera move); joins them."""
    spans: list[tuple[int, int]] = []
    for s in shots:
        if not s.main:
            continue
        if spans and s.start <= spans[-1][1] + 2:
            spans[-1] = (spans[-1][0], s.end)
        else:
            spans.append((s.start, s.end))
    return spans


def propose(path: str) -> dict:
    """Returns fps, frame count, every shot (for debugging) and the proposed segments."""
    spans, fps, total = find_shots(path)
    cap = cv2.VideoCapture(path)
    shots = [Shot(a, b, shot_feature(cap, a, b)) for a, b in spans if b > a]
    cap.release()
    classify(shots)
    min_frames = int(MIN_SEGMENT_S * fps)
    segments = [(a, b) for a, b in merge_main(shots) if b - a + 1 >= min_frames]
    return {"fps": fps, "frames": total, "shots": shots, "segments": segments}


def grab(path: str, frame: int, width: int = 480) -> bytes:
    cap = cv2.VideoCapture(path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame)
    ok, img = cap.read()
    cap.release()
    if not ok:
        return b""
    h = round(img.shape[0] * width / img.shape[1])
    ok, jpg = cv2.imencode(".jpg", cv2.resize(img, (width, h), interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 80])
    return jpg.tobytes() if ok else b""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--sheet", help="write a contact sheet of every shot to this .jpg")
    args = ap.parse_args()
    r = propose(args.video)
    fps = r["fps"]
    print(f"{fps:.3f} fps, {r['frames']} frames, {len(r['shots'])} shots, {len(r['segments'])} segments")
    for i, s in enumerate(r["shots"]):
        tag = "MAIN" if s.main else "    "
        print(f"{i:3d} {tag} {s.start / fps:7.2f}–{s.end / fps:7.2f} s ({s.frames / fps:5.1f} s) diff {s.similarity:5.1f}")
    for a, b in r["segments"]:
        print(f"segment {a / fps:7.2f}–{b / fps:7.2f} s ({(b - a + 1) / fps:5.1f} s)")
    if args.sheet:
        cap = cv2.VideoCapture(args.video)
        tiles = []
        for i, s in enumerate(r["shots"]):
            cap.set(cv2.CAP_PROP_POS_FRAMES, (s.start + s.end) // 2)
            ok, img = cap.read()
            img = cv2.resize(img, (240, 135)) if ok else np.zeros((135, 240, 3), np.uint8)
            colour = (60, 200, 60) if s.main else (60, 60, 220)
            cv2.rectangle(img, (0, 0), (239, 134), colour, 4)
            cv2.putText(img, f"{i} {s.start / fps:.0f}s {s.similarity:.0f}", (6, 20), cv2.FONT_HERSHEY_SIMPLEX, .5, (255, 255, 255), 2)
            tiles.append(img)
        cap.release()
        cols = 8
        while len(tiles) % cols:
            tiles.append(np.zeros((135, 240, 3), np.uint8))
        sheet = np.vstack([np.hstack(tiles[i : i + cols]) for i in range(0, len(tiles), cols)])
        cv2.imwrite(args.sheet, sheet)
        print("sheet:", args.sheet)


if __name__ == "__main__":
    main()
