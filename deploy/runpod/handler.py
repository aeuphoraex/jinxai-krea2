"""RunPod serverless handler — the PUBLIC line: stock core, one frame per job.

This worker is deliberately *not* the studio. It runs the stock Krea 2 Turbo checkpoint with
no uncensored merge, no abliterated encoder and no adapters, because it is the host that
Hugging Face and RunningHub traffic reaches. The studio deck stays on Modal
(`app.py::Krea2.generate`), where the locked contract lives.

The output shape is the same dict the Modal worker returns, so `orbit_engine.render`
normalises it and `commission_render` books the order without a branch for "who drew it":

    {"images": [{"data": "data:image/png;base64,…", "seed": 7, "width": 1280,
                 "height": 1600, "seconds": 31.2}], "steps": 8, "engine": "runpod"}

Schedule is pinned at 8 steps / CFG 0: the core is distilled for it, and a caller must not be
able to ask for another one — the same rule the paid rail enforces. Weights come from the
network volume via `OSS_TURBO`, loaded once per worker and reused while it stays warm.

The age rule is enforced here, before any GPU work, using `orbit_guard` directly so the public
host cannot drift from the private one. The negative prompt is unnecessary at CFG 0 and is
therefore not sent.
"""

from __future__ import annotations

import base64
import io
import os
import time

import orbit_guard
import runpod
from checkpoint import materialize

STEPS = 8
CFG = 0.0
STATE: dict = {}

# Longest a single job may hold a worker before RunPod's own execution timeout is the only
# thing left to save the order.
JOB_TIMEOUT = 600


def pipeline():
    """Load MMDiT + conditioner + VAE once per worker; 12 GB of weights is not per-job work."""
    if not STATE:
        import inference as K

        source = materialize(os.environ.get("OSS_TURBO") or "")
        K.checkpoints["oss_turbo"] = source
        started = time.time()
        dit, ae, encoder = K._pipeline(checkpoint="oss_turbo")
        STATE.update(dit=dit, ae=ae, encoder=encoder, loaded=round(time.time() - started, 1))
        print(f"[runpod] weights loaded in {STATE['loaded']}s")
    return STATE["dit"], STATE["ae"], STATE["encoder"]


def _clamp(value, low, high, default):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _snap(value: int, multiple: int = 64) -> int:
    """Sampling needs multiples of 16 (8x VAE compression x patch 2); 64 is safe and cheap."""
    return max(multiple, int(round(value / multiple)) * multiple)


def handler(job: dict) -> dict:
    event = job.get("input") or {}
    prompt = str(event.get("prompt") or "").strip()[:6000]
    if not prompt:
        return {"error": "prompt is required"}

    # The same rule as the studio, enforced before the container spends a second of GPU.
    reason = orbit_guard.refused(prompt)
    if reason:
        return {"error": f"refused: {reason}"}

    width = _snap(_clamp(event.get("width"), 512, 2048, 1024))
    height = _snap(_clamp(event.get("height"), 512, 2048, 1024))
    seed = int(event.get("seed") or 0)
    if seed < 0:
        seed = 7

    from sampling import sample

    dit, ae, encoder = pipeline()
    started = time.time()
    images = sample(
        dit, ae, encoder, [prompt],
        width=width, height=height, steps=STEPS, guidance=CFG, seed=seed,
    )
    seconds = round(time.time() - started, 2)

    rows = []
    for index, image in enumerate(images):
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        rows.append({
            "data": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii"),
            "seed": seed + index,
            "width": image.width,
            "height": image.height,
            "seconds": seconds,
        })
    print(f"[runpod] {width}x{height} seed {seed} in {seconds}s")
    return {"images": rows, "steps": STEPS, "style_loras": [], "engine": "runpod",
            "line": "public", "checkpoint": "krea2-turbo (stock)"}


if __name__ == "__main__":
    # `test_input` renders one frame without the queue — the smoke test in the README.
    runpod.serverless.start({
        "handler": handler,
        "test_input": {"prompt": os.environ.get("TEST_PROMPT") or "a portrait, window light",
                       "width": 1024, "height": 1024, "seed": 7},
    })
