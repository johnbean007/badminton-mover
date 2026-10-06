"""Badminton Mover worker, run on Modal.

The web app creates a row in `jobs` and calls the `trigger` endpoint with its id; the endpoint
starts the matching function in the background. A sweep every 10 minutes picks up any job whose
trigger call got lost.

    .venv/bin/modal deploy app.py               # publish (prints the trigger URL)
    .venv/bin/modal run app.py::hello           # GPU smoke test
    .venv/bin/modal run app.py::prescan_now --clip-id <uuid>   # pre-scan one clip by hand
    .venv/bin/modal run app.py::gpu_check                      # pose and shuttle models load on the GPU
    .venv/bin/modal run app.py::analyse_now --clip-id <uuid>   # (re-)analyse a clip's chosen rallies
    .venv/bin/modal run app.py::reanalyse_now --clip-id <uuid> # re-run contacts and zones from stored pose

The shuttle tracker's checkpoints live in the Modal volume badminton-mover-models, put there once with
    .venv/bin/modal volume put badminton-mover-models ../spike/vendor/TrackNetV3/ckpts/TrackNet_best.pt /tracknet/
    .venv/bin/modal volume put badminton-mover-models ../spike/vendor/TrackNetV3/ckpts/InpaintNet_best.pt /tracknet/
"""
from __future__ import annotations

import modal

app = modal.App("badminton-mover-worker")

PRESCAN_VERSION = "1"

secrets = [modal.Secret.from_name("badminton-mover-r2"), modal.Secret.from_name("badminton-mover-supabase")]

YOLOX_M = "https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/yolox_m_8xb8-300e_humanart-c2c7a14a.zip"


def fetch_detector():
    from rtmlib import YOLOX

    YOLOX(YOLOX_M, model_input_size=(640, 640), backend="onnxruntime", device="cpu")


base = modal.Image.debian_slim(python_version="3.12")
cpu_deps = base.apt_install("ffmpeg").pip_install(
    "boto3", "httpx", "numpy>=2", "opencv-python-headless>=4.10", "scenedetect>=0.6.6", "fastapi[standard]"
)
cpu_image = cpu_deps.add_local_python_source("prescan", "store", "contacts")
# Person detector for the near-side check, baked into the image so jobs don't download it.
detect_image = (
    cpu_deps.apt_install("libgl1", "libglib2.0-0")
    .pip_install("rtmlib", "onnxruntime")
    .run_function(fetch_detector)
    .add_local_python_source("prescan", "store", "sidecheck")
)


# Pose (ONNX Runtime) and shuttle tracking (PyTorch) on the GPU. Torch's CUDA 13 wheels also
# bring the CUDA and cuDNN libraries onnxruntime-gpu loads; rtmlib pulls in the CPU onnxruntime,
# which is swapped for the GPU build.
models = modal.Volume.from_name("badminton-mover-models", create_if_missing=True)
TRACKNET_DIR = "/models/tracknet"


def fetch_pose_models():
    from rtmlib import Wholebody

    Wholebody(mode="lightweight", backend="onnxruntime", device="cpu")


gpu_image = (
    base.apt_install("ffmpeg", "libgl1", "libglib2.0-0")
    .pip_install("torch", index_url="https://download.pytorch.org/whl/cu130")
    .pip_install("boto3", "httpx", "numpy>=2", "pillow", "rtmlib")
    .run_commands("pip uninstall -y onnxruntime", "pip install onnxruntime-gpu")
    .run_function(fetch_pose_models)
    .add_local_python_source("store", "analyse", "tracknet", "tracknet_model", "contacts")
)


@app.function(image=base, gpu="T4", timeout=120)
def hello() -> dict:
    import platform
    import subprocess

    gpu = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return {"python": platform.python_version(), "gpu": gpu}


# --- Pre-scan -----------------------------------------------------------------------------------

COMMON_FPS = [23.976, 24, 25, 29.97, 30, 48, 50, 59.94, 60, 100, 119.88, 120]


def target_fps(measured: float) -> float:
    """Screen recordings come in at odd, variable rates (30.4 fps); the playback copy is made at the
    nearest standard rate so every frame number means the same moment everywhere."""
    best = min(COMMON_FPS, key=lambda f: abs(f - measured))
    return best if abs(best - measured) / measured < 0.03 else round(measured, 3)


def probe(path: str) -> dict:
    import json
    import subprocess

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,avg_frame_rate,r_frame_rate:format=duration", "-of", "json", path],
        capture_output=True, text=True, check=True,
    ).stdout
    info = json.loads(out)
    s = info["streams"][0]

    def rate(r: str) -> float:
        n, d = r.split("/")
        return float(n) / float(d) if float(d) else 0.0

    fps = rate(s.get("avg_frame_rate", "0/0")) or rate(s.get("r_frame_rate", "0/0"))
    return {"width": s["width"], "height": s["height"], "fps": fps, "duration": float(info["format"]["duration"])}


def transcode(src: str, dst: str, fps: float, height: int) -> None:
    """H.264 720p at the clip's own frame rate, keyframe every second for quick seeking, faststart."""
    import subprocess

    scale = ",scale=-2:720" if height > 720 else ""
    gop = str(max(1, round(fps)))
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", src, "-vf", f"fps={fps}{scale}", "-c:v", "libx264", "-preset", "veryfast",
         "-b:v", "2500k", "-maxrate", "3000k", "-bufsize", "5000k", "-g", gop, "-keyint_min", gop, "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", dst],
        check=True,
    )


def friendly(e: Exception) -> str:
    import subprocess

    if isinstance(e, subprocess.CalledProcessError):
        return "The video couldn't be converted. Try exporting it again as an H.264 MP4."
    return "Something went wrong finding the rallies. Retry, and if it fails again tell the admin."


@app.function(image=cpu_image, secrets=secrets, cpu=4, memory=4096, timeout=30 * 60)
def prescan(job_id: str) -> None:
    import datetime as dt
    import os
    import tempfile
    import traceback
    import uuid

    import cv2
    import scenedetect

    import prescan as ps
    import store

    now = lambda: dt.datetime.now(dt.UTC).isoformat()  # noqa: E731
    claimed = store.update("jobs", {"status": "running", "started_at": now(), "progress": 0.05}, id=f"eq.{job_id}", status="eq.queued")
    if not claimed:
        return  # already running or done
    job = claimed[0]
    store.update("jobs", {"attempts": job["attempts"] + 1}, id=f"eq.{job_id}")
    clip_id = job["clip_id"]
    clips = store.select("clips", id=f"eq.{clip_id}")
    if not clips:
        store.update("jobs", {"status": "failed", "error": "Clip was deleted", "finished_at": now()}, id=f"eq.{job_id}")
        return
    clip = clips[0]
    store.update("clips", {"status": "prescanning", "error": None}, id=f"eq.{clip_id}")
    progress = lambda p: store.update("jobs", {"progress": p}, id=f"eq.{job_id}")  # noqa: E731

    try:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "original" + os.path.splitext(clip["original_key"])[1])
            dst = os.path.join(tmp, "playback.mp4")
            store.download(clip["original_key"], src)
            progress(0.15)

            info = probe(src)
            fps = target_fps(info["fps"])
            transcode(src, dst, fps, info["height"])
            playback_key = f"playback/{clip_id}.mp4"
            store.upload(dst, playback_key, "video/mp4")
            progress(0.6)

            result = ps.propose(dst)
            progress(0.85)

            # A re-run replaces the earlier proposal.
            old = store.remove("rallies", clip_id=f"eq.{clip_id}")
            store.delete([r["thumb_key"] for r in old if r.get("thumb_key")])
            rows = []
            for i, (a, b) in enumerate(result["segments"]):
                rally_id = str(uuid.uuid4())
                thumb_key = f"thumbs/{rally_id}.jpg"
                jpg = ps.grab(dst, (a + b) // 2, width=320)
                if jpg:
                    store.put_bytes(jpg, thumb_key, "image/jpeg")
                rows.append({"id": rally_id, "clip_id": clip_id, "index": i, "start_frame": a, "end_frame": b,
                             "thumb_key": thumb_key if jpg else None})
            store.insert("rallies", rows)

            store.update(
                "clips",
                {"fps": fps, "width": info["width"], "height": info["height"], "duration_s": round(info["duration"], 3),
                 "playback_key": playback_key, "status": "review", "error": None},
                id=f"eq.{clip_id}",
            )
            versions = {"prescan": PRESCAN_VERSION, "scenedetect": scenedetect.__version__, "opencv": cv2.__version__,
                        "measured_fps": round(info["fps"], 3), "segments": len(rows), "shots": len(result["shots"])}
            store.update("jobs", {"status": "done", "progress": 1, "finished_at": now(), "versions": versions, "error": None}, id=f"eq.{job_id}")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        store.update("jobs", {"status": "failed", "error": f"{type(e).__name__}: {e}"[:500], "finished_at": now()}, id=f"eq.{job_id}")
        store.update("clips", {"status": "failed", "error": friendly(e)}, id=f"eq.{clip_id}")


# --- Near-side check -----------------------------------------------------------------------------

@app.function(image=detect_image, secrets=secrets, cpu=4, memory=4096, timeout=20 * 60)
def sidecheck(job_id: str) -> None:
    import datetime as dt
    import os
    import tempfile
    import traceback

    import numpy as np
    from rtmlib import YOLOX

    import sidecheck as sc
    import store

    now = lambda: dt.datetime.now(dt.UTC).isoformat()  # noqa: E731
    claimed = store.update("jobs", {"status": "running", "started_at": now(), "progress": 0.05}, id=f"eq.{job_id}", status="eq.queued")
    if not claimed:
        return
    job = claimed[0]
    store.update("jobs", {"attempts": job["attempts"] + 1}, id=f"eq.{job_id}")
    clip_id = job["clip_id"]
    try:
        clip = store.select("clips", id=f"eq.{clip_id}", select="playback_key,shirt_colour")[0]
        if not clip["shirt_colour"] or not clip["playback_key"]:
            raise ValueError("No shirt colour or playback copy yet")
        rallies = store.select("rallies", clip_id=f"eq.{clip_id}", included="eq.true", calibration_id="not.is.null",
                               select="id,start_frame,end_frame,calibration_id", order="start_frame")
        cals = {c["id"]: np.array(c["homography"], float) for c in store.select("calibrations", clip_id=f"eq.{clip_id}", select="id,homography")}
        shirt = sc.lab(clip["shirt_colour"])
        detector = YOLOX(YOLOX_M, model_input_size=(640, 640), backend="onnxruntime", device="cpu")
        detect = lambda frame: detector(frame)  # noqa: E731

        results = {}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "playback.mp4")
            store.download(clip["playback_key"], path)
            for i, r in enumerate(rallies):
                frames = sc.sample_frames(path, r["start_frame"], r["end_frame"])
                side, votes = sc.check_segment(frames, detect, cals[r["calibration_id"]], shirt)
                store.update("rallies", {"near_side": side}, id=f"eq.{r['id']}")
                results[r["id"]] = votes
                store.update("jobs", {"progress": 0.1 + 0.9 * (i + 1) / max(1, len(rallies))}, id=f"eq.{job_id}")
        store.update("jobs", {"status": "done", "progress": 1, "finished_at": now(), "error": None,
                              "versions": {"sidecheck": "1", "detector": "yolox_m_humanart", "votes": results}}, id=f"eq.{job_id}")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        store.update("jobs", {"status": "failed", "error": f"{type(e).__name__}: {e}"[:500], "finished_at": now()}, id=f"eq.{job_id}")


# --- Analysis: pose, shuttle and hits ------------------------------------------------------------

def subject_id(rally_id: str, player_id: str) -> str:
    """The rally's tracked-player row, kept across re-runs so contacts (and later corrections) stay attached."""
    import store

    rows = store.select("rally_subjects", rally_id=f"eq.{rally_id}", player_id=f"eq.{player_id}", select="id")
    if rows:
        return rows[0]["id"]
    store.remove("rally_subjects", rally_id=f"eq.{rally_id}")  # the clip's player changed
    return store._insert_returning("rally_subjects", {"rally_id": rally_id, "player_id": player_id, "side": "near"})["id"]


def save_contacts(rally_id: str, player_id: str, pose_bytes: bytes, H, hand: str | None) -> dict:
    """Foot contacts and zones from a rally's pose file. Replaces the rule-made contacts; contacts
    members added or corrected stay."""
    import contacts as ct
    import store

    pose = ct.load_pose(pose_bytes)
    found = ct.detect(pose["kp"], pose["conf"], pose["meta"]["fps"], int(pose["frame"][0]), H, hand)
    sid = subject_id(rally_id, player_id)
    store.remove("contacts", subject_id=f"eq.{sid}", source="eq.rules")
    store.insert("contacts", [{**{k: v for k, v in c.items() if k != "image_xy"}, "subject_id": sid, "rules_version": ct.RULES_VERSION}
                              for c in found])
    return ct.summary(found, len(pose["kp"]), pose["meta"]["fps"])


def gpu_models():
    """The pose model on CUDA and the shuttle tracker, with the versions to record."""
    import onnxruntime as ort
    import torch

    import analyse as an
    from tracknet import UPSTREAM, ShuttleTracker

    try:
        ort.preload_dlls()  # CUDA and cuDNN from torch's wheels
    except AttributeError:
        pass
    pose = an.PoseModel(device="cuda")
    tracker = ShuttleTracker(TRACKNET_DIR)
    versions = {"analyse": an.PIPELINE_VERSION, "pose": f"rtmlib {an.POSE_MODE}", "pose_device": pose.providers[0],
                "onnxruntime": ort.__version__, "tracknet": UPSTREAM, "torch": torch.__version__, "shuttle_device": str(tracker.device)}
    return pose, tracker, versions


def analysis_copy(src: str, dst: str, fps: float, height: int, until_s: float) -> None:
    """Full-resolution (up to 1080p) copy at the playback copy's frame rate, so frame numbers match it.
    The 720p playback copy is too small for the far player's pose, which tells who hit the shuttle."""
    import subprocess

    scale = ",scale=-2:1080" if height > 1080 else ""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", src, "-t", f"{until_s:.3f}", "-vf", f"fps={fps}{scale}", "-c:v", "libx264",
         "-preset", "ultrafast", "-crf", "14", "-g", str(max(1, round(fps))), "-pix_fmt", "yuv420p", "-an", dst],
        check=True,
    )


@app.function(image=gpu_image, secrets=secrets, gpu="T4", cpu=8, memory=16384, timeout=40 * 60, volumes={"/models": models})
def analyse(job_id: str) -> None:
    import datetime as dt
    import os
    import tempfile
    import time
    import traceback

    import numpy as np

    import analyse as an
    import store

    now = lambda: dt.datetime.now(dt.UTC).isoformat()  # noqa: E731
    claimed = store.update("jobs", {"status": "running", "started_at": now(), "progress": 0.02}, id=f"eq.{job_id}", status="eq.queued")
    if not claimed:
        return
    job = claimed[0]
    store.update("jobs", {"attempts": job["attempts"] + 1}, id=f"eq.{job_id}")
    clip_id = job["clip_id"]
    last = [0.0]

    def progress(p: float, force: bool = False):
        if force or time.time() - last[0] > 5:
            last[0] = time.time()
            store.update("jobs", {"progress": round(min(p, 0.99), 3)}, id=f"eq.{job_id}")

    try:
        clips = store.select("clips", id=f"eq.{clip_id}", select="original_key,fps,height,shirt_colour,player_id,player:players!clips_player_id_fkey(handedness)")
        if not clips:
            store.update("jobs", {"status": "failed", "error": "Clip was deleted", "finished_at": now()}, id=f"eq.{job_id}")
            return
        clip = clips[0]
        fps = float(clip["fps"])
        hand = (clip.get("player") or {}).get("handedness")
        rallies = store.select("rallies", clip_id=f"eq.{clip_id}", status="in.(queued,analysing)", calibration_id="not.is.null",
                               select="id,start_frame,end_frame,calibration_id", order="start_frame")
        if not rallies:
            raise ValueError("No calibrated rallies waiting for analysis")
        cals = {c["id"]: np.array(c["homography"], float) for c in store.select("calibrations", clip_id=f"eq.{clip_id}", select="id,homography")}
        store.update("clips", {"status": "analysing", "error": None}, id=f"eq.{clip_id}")

        pose, tracker, versions = gpu_models()
        results, done = {}, 0
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "original" + os.path.splitext(clip["original_key"])[1])
            store.download(clip["original_key"], src)
            path = os.path.join(tmp, "analysis.mp4")
            analysis_copy(src, path, fps, int(clip["height"] or 1080), (max(r["end_frame"] for r in rallies) + fps) / fps)
            os.remove(src)
            progress(0.1, force=True)

            for i, r in enumerate(rallies):
                rid = r["id"]
                store.update("rallies", {"status": "analysing"}, id=f"eq.{rid}")
                base_p, span = 0.1 + 0.9 * i / len(rallies), 0.9 / len(rallies)
                try:
                    res = an.analyse_rally(path, r["start_frame"], r["end_frame"], fps, cals[r["calibration_id"]],
                                           clip["shirt_colour"], pose, tracker, lambda f: progress(base_p + span * f))
                    keys = {"pose_key": f"pose/{rid}.npz", "shuttle_key": f"shuttle/{rid}.npz"}
                    pose_bytes = an.pose_npz(res, versions)
                    store.put_bytes(pose_bytes, keys["pose_key"], "application/octet-stream")
                    store.put_bytes(an.shuttle_npz(res, versions), keys["shuttle_key"], "application/octet-stream")
                    store.put_bytes(an.overlay_json(res), f"overlay/{rid}.json", "application/json")
                    # A re-run replaces the tracker's hits; hits members added stay.
                    store.remove("shuttle_hits", rally_id=f"eq.{rid}", source="eq.tracker")
                    store.insert("shuttle_hits", [{"rally_id": rid, "frame": r["start_frame"] + h["index"], "hitter": h["hitter"],
                                                   "confidence": h["confidence"]} for h in res.hits])
                    stats = res.stats
                    stats["contacts"] = save_contacts(rid, clip["player_id"], pose_bytes, cals[r["calibration_id"]], hand)
                    store.update("rallies", {**keys, "status": "ready"}, id=f"eq.{rid}")
                    results[rid] = stats
                    done += 1
                except Exception as e:  # noqa: BLE001
                    traceback.print_exc()
                    store.update("rallies", {"status": "failed"}, id=f"eq.{rid}")
                    results[rid] = {"error": f"{type(e).__name__}: {e}"[:300]}
                progress(base_p + span, force=True)

        if done:
            store.update("clips", {"status": "ready", "error": None}, id=f"eq.{clip_id}")
            store.update("jobs", {"status": "done", "progress": 1, "finished_at": now(), "error": None,
                                  "versions": {**versions, "rallies": results}}, id=f"eq.{job_id}")
        else:
            raise RuntimeError("Every rally failed: " + "; ".join(v["error"] for v in results.values())[:400])
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        store.update("jobs", {"status": "failed", "error": f"{type(e).__name__}: {e}"[:500], "finished_at": now()}, id=f"eq.{job_id}")
        store.update("rallies", {"status": "failed"}, clip_id=f"eq.{clip_id}", status="in.(queued,analysing)")
        store.update("clips", {"status": "failed", "error": "Analysing the rallies failed. Retry, and if it fails again tell the admin."},
                     id=f"eq.{clip_id}")


@app.function(image=cpu_image, secrets=secrets, timeout=15 * 60)
def reanalyse(job_id: str) -> None:
    """Re-runs the later stages (contacts and zones for now) from the stored pose files: seconds on a
    CPU instead of tracking the pose again. The clip stays viewable throughout."""
    import datetime as dt
    import traceback

    import numpy as np

    import contacts as ct
    import store

    now = lambda: dt.datetime.now(dt.UTC).isoformat()  # noqa: E731
    claimed = store.update("jobs", {"status": "running", "started_at": now(), "progress": 0.05}, id=f"eq.{job_id}", status="eq.queued")
    if not claimed:
        return
    job = claimed[0]
    store.update("jobs", {"attempts": job["attempts"] + 1}, id=f"eq.{job_id}")
    clip_id = job["clip_id"]
    try:
        clip = store.select("clips", id=f"eq.{clip_id}", select="player_id,player:players!clips_player_id_fkey(handedness)")[0]
        hand = (clip.get("player") or {}).get("handedness")
        rallies = store.select("rallies", clip_id=f"eq.{clip_id}", status="eq.ready", pose_key="not.is.null",
                               select="id,pose_key,calibration_id", order="start_frame")
        cals = {c["id"]: np.array(c["homography"], float) for c in store.select("calibrations", clip_id=f"eq.{clip_id}", select="id,homography")}
        results = {}
        for i, r in enumerate(rallies):
            results[r["id"]] = save_contacts(r["id"], clip["player_id"], store.get_bytes(r["pose_key"]), cals[r["calibration_id"]], hand)
            store.update("jobs", {"progress": round(0.05 + 0.95 * (i + 1) / len(rallies), 3)}, id=f"eq.{job_id}")
        store.update("jobs", {"status": "done", "progress": 1, "finished_at": now(), "error": None,
                              "versions": {"contacts": ct.RULES_VERSION, "rallies": results}}, id=f"eq.{job_id}")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        store.update("jobs", {"status": "failed", "error": f"{type(e).__name__}: {e}"[:500], "finished_at": now()}, id=f"eq.{job_id}")


@app.function(image=gpu_image, gpu="T4", timeout=600, volumes={"/models": models})
def gpu_check() -> dict:
    """Loads both models on the GPU and times them on blank frames."""
    import time

    import numpy as np

    from tracknet import shrink

    pose, tracker, versions = gpu_models()
    frame = np.full((1008, 1920, 3), 90, np.uint8)
    t = time.time()
    for _ in range(20):
        pose.keypoints(frame, np.float32([[800, 400, 1000, 900]]))
    pose_fps = 20 / (time.time() - t)
    small = np.stack([shrink(frame)] * 64)
    t = time.time()
    tracker.track(small, 1920, 1008)
    result = {**versions, "pose_crops_per_s": round(pose_fps, 1), "shuttle_frames_per_s": round(64 / (time.time() - t), 1)}
    print(result)
    return result


RUNNERS = {"prescan": prescan, "sidecheck": sidecheck, "analyse": analyse, "reanalyse": reanalyse}


# --- Starting jobs ------------------------------------------------------------------------------

@app.function(image=cpu_image, secrets=[*secrets, modal.Secret.from_name("badminton-mover-worker-token")])
@modal.asgi_app(label="badminton-mover-trigger")
def trigger():
    import hmac
    import os

    from fastapi import FastAPI, Header, HTTPException

    import store

    web = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @web.post("/jobs/{job_id}")
    def start(job_id: str, authorization: str = Header("")):
        expected = "Bearer " + os.environ["WORKER_TOKEN"].strip()
        if not hmac.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(401)
        jobs = store.select("jobs", id=f"eq.{job_id}", select="id,type,status")
        if not jobs or jobs[0]["type"] not in RUNNERS:
            raise HTTPException(404)
        if jobs[0]["status"] == "queued":
            RUNNERS[jobs[0]["type"]].spawn(job_id)
        return {"ok": True}

    return web


@app.function(image=cpu_image, secrets=secrets, schedule=modal.Cron("*/10 * * * *"))
def sweep() -> None:
    """Starts queued jobs whose trigger call never arrived, and fails jobs that died mid-run."""
    import datetime as dt

    import store

    now = dt.datetime.now(dt.UTC)
    for job in store.select("jobs", status="eq.queued", created_at=f"lt.{(now - dt.timedelta(minutes=2)).isoformat()}"):
        if job["type"] in RUNNERS and job["attempts"] < 3:
            RUNNERS[job["type"]].spawn(job["id"])
    for job in store.select("jobs", status="eq.running", started_at=f"lt.{(now - dt.timedelta(minutes=45)).isoformat()}"):
        store.update("jobs", {"status": "failed", "error": "Timed out", "finished_at": now.isoformat()}, id=f"eq.{job['id']}")
        if job["type"] == "prescan":
            store.update("clips", {"status": "failed", "error": "Finding the rallies took too long. Retry."}, id=f"eq.{job['clip_id']}")
        elif job["type"] == "analyse":
            store.update("rallies", {"status": "failed"}, clip_id=f"eq.{job['clip_id']}", status="in.(queued,analysing)")
            store.update("clips", {"status": "failed", "error": "Analysing the rallies took too long. Retry."}, id=f"eq.{job['clip_id']}")


@app.function(image=cpu_image, secrets=secrets)
def queue_job(clip_id: str, job_type: str) -> str:
    import store

    rows = store._db().post("/jobs", json={"clip_id": clip_id, "type": job_type}, headers={"Prefer": "return=representation"})
    rows.raise_for_status()
    return rows.json()[0]["id"]


@app.function(image=cpu_image, secrets=secrets)
def job_summary(job_id: str) -> dict:
    import store

    job = store.select("jobs", id=f"eq.{job_id}")[0]
    clip = store.select("clips", id=f"eq.{job['clip_id']}", select="status,error,fps,playback_key")[0]
    rallies = store.select("rallies", clip_id=f"eq.{job['clip_id']}", select="index,start_frame,end_frame", order="index")
    return {"job": {k: job[k] for k in ("status", "error", "versions", "started_at", "finished_at")}, "clip": clip, "rallies": rallies}


@app.function(image=cpu_image, secrets=secrets)
def requeue_analysis(clip_id: str) -> str:
    """Puts a clip's analysed (or failed) rallies back in the queue and records an analyse job."""
    import store

    store.update("rallies", {"status": "queued"}, clip_id=f"eq.{clip_id}", status="in.(ready,failed,analysing)")
    store.update("clips", {"status": "queued", "error": None}, id=f"eq.{clip_id}")
    return queue_job.local(clip_id, "analyse")


@app.local_entrypoint()
def analyse_now(clip_id: str):
    """Re-analyses one clip's rallies by hand, waiting for it to finish."""
    job_id = requeue_analysis.remote(clip_id)
    analyse.remote(job_id)
    summary = job_summary.remote(job_id)
    print({k: summary["job"][k] for k in ("status", "error")})
    for rid, stats in (summary["job"]["versions"] or {}).get("rallies", {}).items():
        print(rid[:8], stats)


@app.local_entrypoint()
def reanalyse_now(clip_id: str):
    """Re-runs contacts and zones for one clip from its stored pose files, waiting for it to finish."""
    job_id = queue_job.remote(clip_id, "reanalyse")
    reanalyse.remote(job_id)
    summary = job_summary.remote(job_id)
    print({k: summary["job"][k] for k in ("status", "error")})
    for rid, stats in (summary["job"]["versions"] or {}).get("rallies", {}).items():
        print(rid[:8], stats)


@app.local_entrypoint()
def prescan_now(clip_id: str):
    """Queues and runs a pre-scan for one clip, waiting for it to finish."""
    job_id = queue_job.remote(clip_id, "prescan")
    prescan.remote(job_id)
    print(job_summary.remote(job_id))
