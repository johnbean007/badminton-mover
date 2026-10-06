"""Badminton Mover GPU worker, run on Modal.

Skeleton for now: proves we can start a GPU container and get a result back.
Pose (RTMW) and shuttle (TrackNetV3) tracking from spike/ move in here later.

    .venv/bin/modal run app.py      # run hello() once on a T4
"""

import modal

app = modal.App("badminton-mover-worker")

image = modal.Image.debian_slim(python_version="3.12")


@app.function(image=image, gpu="T4", timeout=120)
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


@app.local_entrypoint()
def main():
    print(hello.remote())
