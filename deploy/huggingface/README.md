---
title: Velvet Studio
emoji: 🖤
colorFrom: purple
colorTo: gray
sdk: gradio
sdk_version: 5.49.1
app_file: app.py
pinned: false
license: other
short_description: Krea 2 portrait studio — one prompt, one frame
---

# Velvet Studio

A free front desk for the **public** Krea 2 line. The Space holds no weights and runs no
diffusion: it submits one job to the RunPod serverless worker and returns the frame. That keeps
the Space on CPU hardware while the GPU work stays on a host billed per second.

It renders the **stock** Krea 2 Turbo core — no unfiltered merge, no abliterated encoder, no
adapters. The studio deck (her adapter, the house pair, 4K, video) stays on the private
deployment and is not reachable from here.

## Secrets

Set both in *Settings → Variables and secrets*:

| Name | What | Visible? |
|---|---|---|
| `RUNPOD_ENDPOINT_ID` | the id `deploy/runpod/publish.py --apply` printed | variable |
| `RUNPOD_API_KEY` | a key that may call that endpoint | **secret** |

Without them the Space loads and says it is not connected; it never falls back to another
backend.

## Hardware

CPU Basic is enough — the Space only makes HTTP calls. Note that Gradio Spaces on compute
require a paid plan (PRO for personal accounts); free accounts in good standing can host up to
two Gradio Spaces on ZeroGPU, which also works here (the Space simply never asks for a GPU).

## What this Space is not

* Not a renderer. There is no GPU and no model here.
* Not the escort gallery, the chat, or the adapter marketplace.
* Not a place for the adult deck: Hugging Face's content policy prohibits sexual content
  without consent and NCII, and the Space must never be pointed at `/aigf/api/generate`.

## Deploy

```bash
hf auth login                                  # once
hf upload <user>/velvet-studio deploy/huggingface . --repo-type space
```

Or create the Space in the UI and drag this folder in. Then set the two secrets above.
