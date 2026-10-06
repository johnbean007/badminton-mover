"""Shuttle tracking with TrackNetV3 (MIT licence, https://github.com/qaz812345/TrackNetV3), inference only.

A port of the repo's predict.py (with --eval_mode weight and an InpaintNet checkpoint) for frames
already in memory, so the worker needs neither its data loaders nor its training dependencies:

1. TrackNet sees every run of `seq_len` frames (sliding by one) plus the rally's median background
   and returns a heatmap per frame. Each frame's heatmaps from the windows that contain it are
   averaged, weighted towards the middle of the window; the shuttle is the centre of the biggest
   blob above 0.5.
2. InpaintNet fills short stretches where the shuttle was lost mid-flight, from the positions
   either side, averaged over windows the same way.

One difference from upstream: frames are shrunk to 512×288 before the background median is taken
(upstream takes it at full size, then shrinks), which keeps a long rally's memory use small.
"""
from __future__ import annotations

import math
import os

import cv2
import numpy as np
import torch
from PIL import Image

from tracknet_model import InpaintNet, TrackNet

UPSTREAM = "qaz812345/TrackNetV3@6eda442"
HEIGHT, WIDTH = 288, 512
COOR_TH = 50 / math.sqrt(HEIGHT**2 + WIDTH**2)  # normalised positions this close to (0, 0) mean "not seen"
BATCH = 16


def pick_device() -> torch.device:
    if os.environ.get("TRACKNET_DEVICE"):
        return torch.device(os.environ["TRACKNET_DEVICE"])
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def shrink(frame_bgr: np.ndarray) -> np.ndarray:
    """One frame as TrackNet's input: RGB, 512×288, resized the way upstream does (Pillow bicubic)."""
    return np.asarray(Image.fromarray(frame_bgr[:, :, ::-1]).resize((WIDTH, HEIGHT)))


def ensemble_weight(seq_len: int) -> torch.Tensor:
    w = torch.ones(seq_len)
    for i in range(math.ceil(seq_len / 2)):
        w[i] = w[seq_len - i - 1] = i + 1
    return w / w.sum()


def biggest_blob(heatmap: np.ndarray) -> tuple[int, int]:
    """Centre (x, y) of the largest-area box around a blob in a binary 512×288 heatmap, or (0, 0)."""
    if not heatmap.any():
        return 0, 0
    cnts, _ = cv2.findContours(heatmap.astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    x, y, w, h = max((cv2.boundingRect(c) for c in cnts), key=lambda r: r[2] * r[3])
    return int(x + w / 2), int(y + h / 2)


def inpaint_mask(y: np.ndarray, vis: np.ndarray, th_h: float) -> np.ndarray:
    """Which missing stretches InpaintNet should fill: gaps with the shuttle seen below the top 5% of
    the frame on both sides (upstream's generate_inpaint_mask)."""
    mask = np.zeros(len(y))
    i = j = 0
    while j < len(vis):
        while i < len(vis) - 1 and vis[i] == 1:
            i += 1
        j = i
        while j < len(vis) - 1 and vis[j] == 0:
            j += 1
        if j == i:
            break
        if i == 0 and y[j] > th_h:
            mask[:j] = 1
        elif (i > 1 and y[i - 1] > th_h) and (j < len(vis) and y[j] > th_h):
            mask[i:j] = 1
        i = j
    return mask


class Ensemble:
    """Averages each frame's predictions from the windows that contain it: weighted when all
    `seq_len` windows exist, a plain mean near the ends of the rally (as upstream)."""

    def __init__(self, n: int, seq_len: int):
        self.n, self.L = n, seq_len
        self.weight = ensemble_weight(seq_len)
        self.weighted: dict[int, torch.Tensor] = {}
        self.plain: dict[int, torch.Tensor] = {}

    def windows_containing(self, t: int) -> int:
        return min(t, self.n - self.L) - max(0, t - self.L + 1) + 1

    def add(self, start: int, preds: torch.Tensor) -> list[tuple[int, torch.Tensor]]:
        """Adds one window's predictions (seq_len, ...) and returns the frames now complete."""
        for p in range(self.L):
            t = start + p
            self.weighted[t] = self.weighted.get(t, 0) + self.weight[p] * preds[p]
            self.plain[t] = self.plain.get(t, 0) + preds[p]
        last_window = start == self.n - self.L
        done = range(start, self.n) if last_window else [start]
        out = []
        for t in done:
            k = self.windows_containing(t)
            out.append((t, self.weighted[t] if k == self.L else self.plain[t] / k))
            del self.weighted[t], self.plain[t]
        return out


class ShuttleTracker:
    def __init__(self, ckpt_dir: str, device: torch.device | None = None):
        self.device = device or pick_device()
        # weights_only: the checkpoints are plain tensors and settings, so nothing in them can run code.
        t = torch.load(os.path.join(ckpt_dir, "TrackNet_best.pt"), map_location=self.device, weights_only=True)
        self.seq_len, self.bg_mode = int(t["param_dict"]["seq_len"]), t["param_dict"]["bg_mode"]
        if self.bg_mode not in ("", "concat"):
            raise ValueError(f"TrackNet background mode {self.bg_mode!r} isn't supported by this port")
        in_dim = (self.seq_len + 1) * 3 if self.bg_mode == "concat" else self.seq_len * 3
        self.tracknet = TrackNet(in_dim=in_dim, out_dim=self.seq_len).to(self.device).eval()
        self.tracknet.load_state_dict(t["model"])
        i = torch.load(os.path.join(ckpt_dir, "InpaintNet_best.pt"), map_location=self.device, weights_only=True)
        self.inpaint_len = int(i["param_dict"]["seq_len"])
        self.inpaintnet = InpaintNet().to(self.device).eval()
        self.inpaintnet.load_state_dict(i["model"])

    @torch.no_grad()
    def track(self, small: np.ndarray, width: int, height: int) -> dict[str, np.ndarray]:
        """small: (n, 288, 512, 3) RGB frames from shrink(). width, height: the real frame size.
        Returns per-frame x, y in real-frame pixels (NaN where not seen), score (peak heatmap value)
        and inpainted (filled by InpaintNet rather than seen)."""
        n, L = len(small), self.seq_len
        nan = np.full(n, np.nan, np.float32)
        if n < max(L, self.inpaint_len):
            return {"x": nan, "y": nan.copy(), "score": np.zeros(n, np.float32), "inpainted": np.zeros(n, bool)}
        sx, sy = width / WIDTH, height / HEIGHT

        frames = torch.from_numpy(np.ascontiguousarray(small)).permute(0, 3, 1, 2).to(self.device)  # uint8 (n, 3, H, W)
        median = torch.from_numpy(np.median(small, 0).astype(np.uint8)).permute(2, 0, 1).to(self.device)
        xs, ys, score = np.zeros(n), np.zeros(n), np.zeros(n, np.float32)
        ens = Ensemble(n, L)
        starts = list(range(n - L + 1))
        for b in range(0, len(starts), BATCH):
            batch = starts[b : b + BATCH]
            x = torch.stack([frames[s : s + L].reshape(L * 3, HEIGHT, WIDTH) for s in batch])
            if self.bg_mode == "concat":
                x = torch.cat([median.expand(len(batch), 3, HEIGHT, WIDTH), x], 1)
            heat = self.tracknet(x.float() / 255.0).cpu()  # (B, L, H, W) probabilities
            for s, h in zip(batch, heat):
                for t, hm in ens.add(s, h):
                    hm = hm.numpy()
                    cx, cy = biggest_blob(hm > 0.5)
                    xs[t], ys[t], score[t] = int(cx * sx), int(cy * sy), float(hm.max())
        vis = ~((xs == 0) & (ys == 0))

        # InpaintNet over the positions, normalised to 0-1.
        mask = inpaint_mask(ys, vis.astype(int), th_h=height * 0.05)
        coor = torch.from_numpy(np.stack([xs / width, ys / height], 1)).float()
        m = torch.from_numpy(mask).float()[:, None]
        L2 = self.inpaint_len
        ens2 = Ensemble(n, L2)
        fixed = np.zeros((n, 2))
        starts = list(range(n - L2 + 1))
        for b in range(0, len(starts), BATCH):
            batch = starts[b : b + BATCH]
            c = torch.stack([coor[s : s + L2] for s in batch])
            mm = torch.stack([m[s : s + L2] for s in batch])
            out = self.inpaintnet(c.to(self.device), mm.to(self.device)).cpu()
            out = out * mm + c * (1 - mm)
            out[(out[..., 0] < COOR_TH) & (out[..., 1] < COOR_TH)] = 0.0
            for s, o in zip(batch, out):
                for t, p in ens2.add(s, o):
                    fixed[t] = 0.0 if (p[0] < COOR_TH and p[1] < COOR_TH) else p.numpy()
        fx = (fixed[:, 0] * WIDTH * sx).astype(int)
        fy = (fixed[:, 1] * HEIGHT * sy).astype(int)
        seen = ~((fx == 0) & (fy == 0))
        return {
            "x": np.where(seen, fx, np.nan).astype(np.float32),
            "y": np.where(seen, fy, np.nan).astype(np.float32),
            "score": np.where(seen, score, 0).astype(np.float32),
            "inpainted": seen & (mask > 0) & ~vis,
        }
