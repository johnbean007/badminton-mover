"""Badminton Mover worker, run on Modal.

The web app creates a row in `jobs` and calls the `trigger` endpoint with its id; the endpoint
starts the matching function in the background. A sweep every 10 minutes picks up any job whose
trigger call got lost.

    .venv/bin/modal deploy app.py               # publish (prints the trigger URL)
    .venv/bin/modal run app.py::hello           # GPU smoke test
    .venv/bin/modal run app.py::prescan_now --clip-id <uuid>   # pre-scan one clip by hand
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
cpu_image = cpu_deps.add_local_python_source("prescan", "store")
# Person detector for the near-side check, baked into the image so jobs don't download it.
detect_image = (
    cpu_deps.apt_install("libgl1", "libglib2.0-0")
    .pip_install("rtmlib", "onnxruntime")
    .run_function(fetch_detector)
    .add_local_python_source("prescan", "store", "sidecheck")
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


RUNNERS = {"prescan": prescan, "sidecheck": sidecheck}


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
        store.update("clips", {"status": "failed", "error": "Finding the rallies took too long. Retry."}, id=f"eq.{job['clip_id']}")


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


@app.local_entrypoint()
def prescan_now(clip_id: str):
    """Queues and runs a pre-scan for one clip, waiting for it to finish."""
    job_id = queue_job.remote(clip_id, "prescan")
    prescan.remote(job_id)
    print(job_summary.remote(job_id))
