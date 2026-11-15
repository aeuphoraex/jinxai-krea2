"""Velvet Studio — the Hugging Face Space.

The Space is the front desk, not the engine: it renders nothing itself and holds no weights.
It submits one job to the **RunPod public worker** (`deploy/runpod/handler.py`), which runs the
stock Krea 2 Turbo core with no adapters and no unfiltered merge, and shows the frame that
comes back.

That target is the point. An earlier version of this Space proxied `/aigf/api/generate` on the
studio deployment, and that route hard-sets `variant=uncensored`, `encoder=abliterated` and
`want_lora=True`. A public Space in front of the unfiltered deck with the house
`bimbo`/`breastshape` adapters always fitted is a content-policy problem on the platform that
hosts it for free — so the Space now talks only to the public line.

Set these in *Settings → Variables and secrets*:

    RUNPOD_ENDPOINT_ID   the endpoint id publish.py printed (e.g. 8k3m2p1qwerty)
    RUNPOD_API_KEY       a key that may call it — mark it a **secret**
"""

from __future__ import annotations

import base64
import io
import os
import re
import time

import gradio as gr
import requests
from PIL import Image

ENDPOINT = (os.environ.get("RUNPOD_ENDPOINT_ID") or "").strip()
KEY = (os.environ.get("RUNPOD_API_KEY") or "").strip()
API = "https://api.runpod.ai/v2/" + ENDPOINT if ENDPOINT else ""
JOBS = API + "/run"
STATUS = API + "/status/"

QUALITY = (
    "raw photo, photorealistic, ultra-detailed natural skin texture with visible pores and "
    "fine vellus hair, realistic subsurface scattering, natural makeup, no plastic or "
    "airbrushed skin, sharp focus on the eyes, 85mm lens, subtle film grain, clean "
    "background bokeh, no motion blur"
)

SIZES = {"Portrait 4:5": (1280, 1600), "Square": (1280, 1280), "Wide 16:9": (1600, 900)}

# The floor of the age rule, mirroring orbit_guard on the server. Duplicated because a Space
# cannot import from the repo; the worker enforces the full list regardless, so this is a
# courtesy that saves a round trip, not the control.
_DENY = (
    (re.compile(r"\b(child|children|kid|kids|toddler|infant|baby)\b", re.I),
     "minors are never a render subject"),
    (re.compile(r"\b(underage|under-age|under age|preteen|pre-teen|prepubescent)\b", re.I),
     "minors are never a render subject"),
    (re.compile(r"\b(loli|lolicon|shota|shotacon|jailbait)\b", re.I),
     "minors are never a render subject"),
    (re.compile(r"\b(schoolgirl|schoolboy|school uniform)\b", re.I),
     "school-age framing is not available"),
    (re.compile(r"\b(real person|celebrity|deepfake|passport photo)\b", re.I),
     "real people are not render subjects"),
    (re.compile(r"\b(rape|non-?consensual|drugged|unconscious)\b", re.I),
     "non-consensual framing is refused"),
)


def refuse(prompt: str) -> str:
    for pattern, reason in _DENY:
        if pattern.search(prompt or ""):
            return reason
    return ""


def _headers() -> dict:
    return {"authorization": KEY, "content-type": "application/json"}


def _wait(job_id: str, budget: int = 300) -> dict:
    """Poll until the worker finishes. RunPod queues, so the budget is generous and honest."""
    deadline = time.time() + budget
    while time.time() < deadline:
        resp = requests.get(STATUS + job_id, headers=_headers(), timeout=20)
        if resp.status_code != 200:
            return {"error": f"status {resp.status_code}: {resp.text[:160]}"}
        got = resp.json()
        state = str(got.get("status") or "").upper()
        if state == "COMPLETED":
            return got
        if state in ("FAILED", "CANCELLED", "TIMED_OUT"):
            return {"error": f"{state.lower()}: {str(got.get('error'))[:200]}"}
        time.sleep(2.0)
    return {"error": "the worker is still queued — try again in a minute"}


def draw(prompt: str, size: str, seed: int):
    """One frame, or the server's own error text. Nothing is retried here on purpose."""
    prompt = (prompt or "").strip()
    if not prompt:
        return None, "Type a prompt first."
    if not ENDPOINT or not KEY:
        return None, ("This Space is not connected yet: set RUNPOD_ENDPOINT_ID and "
                      "RUNPOD_API_KEY in the Space's secrets.")
    reason = refuse(prompt)
    if reason:
        return None, f"Refused: {reason}."

    width, height = SIZES.get(size, SIZES["Portrait 4:5"])
    body = {
        "input": {
            "prompt": prompt + ", " + QUALITY,
            "width": width,
            "height": height,
            "seed": int(seed),
        }
    }
    try:
        resp = requests.post(JOBS, json=body, headers=_headers(), timeout=60)
    except requests.RequestException as exc:
        return None, f"The studio is unreachable: {type(exc).__name__}"
    if resp.status_code != 200:
        return None, f"Refused ({resp.status_code}): {resp.text[:200]}"

    got = _wait(str(resp.json().get("id") or ""))
    if got.get("error"):
        return None, got["error"]

    out = got.get("output") or {}
    if isinstance(out, dict) and out.get("error"):
        return None, f"Worker refused: {out['error']}"
    first = ((out.get("images") if isinstance(out, dict) else None) or [{}])[0]
    data = str((first or {}).get("data") or "")
    if not data:
        return None, "The renderer returned no image."
    raw = base64.b64decode(data.split(",", 1)[-1])
    return Image.open(io.BytesIO(raw)), (
        f"{first.get('width')}x{first.get('height')} · seed {first.get('seed')} · "
        f"{first.get('seconds')}s on {out.get('engine') or 'runpod'} · stock core"
    )


with gr.Blocks(title="Velvet Studio") as demo:
    gr.Markdown(
        "# Velvet Studio\n"
        "One prompt, one frame. Photoreal, uncropped, delivered.\n\n"
        "*The stock Krea 2 Turbo core, on RunPod. Every character is an adult.*"
    )
    with gr.Row():
        with gr.Column():
            prompt = gr.Textbox(label="Who and where", lines=4,
                                placeholder="A woman in a rain-lit alley, black coat, looking back")
            size = gr.Dropdown(list(SIZES), value="Portrait 4:5", label="Frame")
            seed = gr.Number(value=7, precision=0, label="Seed")
            go = gr.Button("Render", variant="primary")
        with gr.Column():
            out = gr.Image(label="Frame", type="pil")
            meta = gr.Markdown()
    go.click(draw, [prompt, size, seed], [out, meta])
    prompt.submit(draw, [prompt, size, seed], [out, meta])

if __name__ == "__main__":
    demo.launch()
