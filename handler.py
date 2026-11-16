"""RunPod serverless handler — the PUBLIC line: stock core, one frame per job.

This is the repository's root `handler.py`, because that is the file the RunPod worker
convention looks for: `python handler.py` renders one frame from `test_input.json`, and the
image runs `CMD ["python", "-u", "/opt/handler.py"]`. `deploy/runpod/handler.py` re-exports this
module for the scripts that already import it from there.

The worker is deliberately *not* the studio. It runs the stock Krea 2 Turbo checkpoint with
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

A 48 GB card holds the DiT, the Qwen3-VL encoder and the VAE at once (34.3 GB of weights). A
32 GB card does not, and the DiT alone is 26.28 GB, so once the renderer is loaded the encoder
is parked on the CPU and streamed through the GPU for each prompt (`deploy/runpod/lowvram.py`).
That is what makes a 32 GB pool usable at all; the render is identical either way, because the
sampler takes the conditioning the encode produced.

The age rule is enforced here, before any GPU work, using `orbit_guard` directly so the public
host cannot drift from the private one. The negative prompt is unnecessary at CFG 0 and is
therefore not sent.
"""

from __future__ import annotations

import base64
import io
import os
import pathlib
import sys
import time

# `checkpoint.py` and `lowvram.py` sit beside the worker sources in `deploy/runpod/` in the
# repository and at `/opt/*.py` inside the image. Adding the repository folder to the path keeps
# this one file runnable from either place, so `python handler.py` behaves the way RunPod's
# worker template does.
_DEPLOY = pathlib.Path(__file__).resolve().parent / "deploy" / "runpod"
if _DEPLOY.is_dir():
    sys.path.insert(0, str(_DEPLOY))

import lowvram  # noqa: E402
import orbit_guard  # noqa: E402
import runpod  # noqa: E402
from checkpoint import materialize  # noqa: E402

STEPS = 8
CFG = 0.0
STATE: dict = {}

TEST_INPUT = {"prompt": os.environ.get("TEST_PROMPT") or "a portrait, window light",
              "width": 1024, "height": 1024, "seed": 7}

# Longest a single job may hold a worker before RunPod's own execution timeout is the only
# thing left to save the order.
JOB_TIMEOUT = 600


def free_vram() -> int:
    """VRAM available right now, or 0 when there is no device to ask."""
    try:
        import torch

        free, _ = torch.cuda.mem_get_info()
        return int(free)
    except Exception:                       # noqa: BLE001 - no device, no measurement
        return 0


def _no_dynamo() -> None:
    """Run the DiT eagerly: this image has no C compiler for Inductor.

    `krea2/mmdit.py` decorates `RMSNorm`, `PositionalEncoding` and `LastLayer` with
    `@torch.compile(fullgraph=True)`, so the first forward hands a graph to Inductor, and Triton's
    driver compiles a small C extension with `cc` before it can emit a kernel. The base image is
    `nvidia/cuda:*-runtime`, which deliberately carries no toolchain, so a live Hub run died at
    the first frame with "Failed to find C compiler".

    Two switches, called before `inference` (and therefore `mmdit`) is imported: `torch.compile`
    becomes an identity decorator, so the three decorated methods are plain Python, and
    `torch._dynamo.config.disable` is set for anything that reaches dynamo another way. The
    identity decorator is the one that matters — a template's environment can turn a flag off, but
    it cannot put the compiled path back. The image also exports `TORCH_COMPILE_DISABLE=1`, which
    is the name `torch/_dynamo/config.py` reads. None of this touches the Modal studio, which keeps
    the compiled path because its image does carry a toolchain.
    """
    try:
        import torch
    except Exception:                       # noqa: BLE001 - no torch here, nothing to disable
        return

    def _eager(fn=None, *args, **kwargs):
        """Stand-in for `torch.compile`: hand the decorated function straight back."""
        if fn is None:
            return lambda inner: inner
        return fn

    torch.compile = _eager
    try:
        import torch._dynamo as dynamo

        dynamo.config.disable = True
    except Exception:                       # noqa: BLE001 - the decorator swap already covers us
        pass


def pipeline():
    """Load MMDiT + conditioner + VAE once per worker; 12 GB of weights is not per-job work.

    How the encoder is kept decides which cards can run a job. A card with room for it beside
    the renderer keeps it where `_pipeline` put it; a card without room gets it parked, and
    `handler` streams it per prompt. The decision is made from free VRAM *after* the loader has
    run, because that is the only reading that reflects what the renderer actually took.
    """
    if not STATE:
        _no_dynamo()
        import inference as K

        source = materialize(os.environ.get("OSS_TURBO") or "")
        K.checkpoints["oss_turbo"] = source
        started = time.time()
        dit, ae, encoder = K._pipeline(checkpoint="oss_turbo")
        layers = lowvram.decoder_layers(encoder)
        mode = "resident"
        if layers and lowvram.strategy(free_vram()) == "streamed":
            mode = "streamed"
            lowvram.park(encoder)
        STATE.update(dit=dit, ae=ae, encoder=encoder, layers=layers, mode=mode,
                     loaded=round(time.time() - started, 1))
        print(f"[runpod] weights loaded in {STATE['loaded']}s, encoder {mode} "
              f"({len(layers)} layers, {free_vram() / 1e9:.1f} GB free)")
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
        # A refusal is an answer, not a broken job. RunPod marks any job whose output carries an
        # `error` key as FAILED, so a refusal returned that way can never be a passing Hub test —
        # and the listing's second test exists to prove exactly this path. `refused` keeps the
        # meaning while the job completes, and the empty `images` list says nothing rendered.
        return {"refused": reason, "images": [], "steps": STEPS, "engine": "runpod",
                "line": "public", "checkpoint": "krea2-turbo (stock)"}

    width = _snap(_clamp(event.get("width"), 512, 2048, 1024))
    height = _snap(_clamp(event.get("height"), 512, 2048, 1024))
    seed = int(event.get("seed") or 0)
    if seed < 0:
        seed = 7

    from sampling import sample

    dit, ae, encoder = pipeline()
    started = time.time()

    # On a streamed worker the encode is the only part of the job that needs the encoder, so it
    # happens first, against the renderer's already-resident weights, and the conditioning is
    # handed to the sampler. The encode stays inside `seconds` for the same reason the resident
    # path does: it is work this job made the GPU do.
    text = None
    if STATE.get("mode") == "streamed":
        text = lowvram.encode(encoder, STATE.get("layers") or [], lambda: encoder([prompt]))

    images = sample(
        dit, ae, encoder, [prompt],
        width=width, height=height, steps=STEPS, guidance=CFG, seed=seed, text=text,
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
    # Without the queue: one frame, the smoke test in the README. This is also what
    # `python handler.py` does locally, with `test_input.json` as the SDK's fallback.
    runpod.serverless.start({"handler": handler, "test_input": TEST_INPUT})
