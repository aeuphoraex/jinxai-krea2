# ORBITAL graphs

Two graphs, both stock ComfyUI (no custom nodes), pinned to this deck's settings:

| File | Replicates | Load with |
|---|---|---|
| `orbital_portrait.json` | `/api/generate` — still frames, stacked drift adapters, 8 steps, echo branch | drag onto the canvas |
| `orbital_motion.json` | `/api/video` — motion, ascent/descent core pair, one drift adapter per expert | drag onto the canvas |

Regenerate or validate:

```bash
python comfy/build_workflows.py           # rewrite both JSON files
python comfy/build_workflows.py --check   # structural check: slots, links, class names
```

## Feature map

| Deck behaviour | Graph equivalent |
|---|---|
| unfiltered core (merged weights) | `UNETLoader` — point `unet_name` at your own single-file core |
| text bridge, 12-layer tap | `CLIPLoader` with `type = krea2` |
| latent decoder | `VAELoader` |
| `steps: 8`, `cfg: 0` (guidance off) | `KSampler` 8 steps, `cfg 1.0`, `euler` + `simple`, negative via `ConditioningZeroOut` |
| `style_loras` stack | one `LoraLoaderModelOnly` per adapter, chained, own `strength_model` |
| `style_scale` | that node's `strength_model` |
| trigger words prepended | write them first in the positive prompt (see limits below) |
| `mode: i2i`, `strength: 0.6` | `LoadImage → VAEEncode → KSampler` at `denoise 0.6` (muted echo branch) |
| `upscale: 3840` (tiled re-denoise) | not wired — `ImageUpscaleWithModel` or Ultimate SD Upscale at ~0.3 denoise |
| `/api/video` 480p, 81 frames | `WanImageToVideo` 832×480, length 81 (4n+1), `SaveWEBM` at 24 fps |
| motion ascent/descent experts | two `UNETLoader`s + `ModelSamplingSD3` shift 5.0, `KSamplerAdvanced` split 12/12 |
| `lora` (per-frame adapter imprint) | **no equivalent** — needs a trainer, not a graph |

## Weights

Model files go in `models/` of the ComfyUI install. These filenames are resolved by string, so
they keep their real names even though every label around them is rebranded:

```
models/diffusion_models/krea2_turbo_fp8_scaled.safetensors
models/text_encoders/qwen3vl_4b_fp8_scaled.safetensors
models/vae/qwen_image_vae.safetensors

models/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors
models/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors
models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors
models/vae/wan_2.1_vae.safetensors
```

Adapters already downloaded into the volume are the same files the loader nodes want:

```bash
modal volume get krea2-weights loras/Annai-Krea2.safetensors ./Annai-Krea2.safetensors
```

Motion adapters live under `loras/video/` and go to the same destination folder.

## What is not replicable as a graph

Stock ComfyUI has no registry, no server and no trainer, so four things stay in the deck:

1. **Per-frame adapter imprint.** `Krea2LoRA` fits a rank-8, 40-step adapter on each frame and
   exports it with `ss_network_*` metadata. ComfyUI has training nodes in third-party packs,
   but nothing that fits and exports per render.
2. **Trigger-aware expansion.** The deck appends each stacked adapter's trigger before *and*
   after the expander. Stock `TextGenerate` has no notion of adapter triggers, so the prompt
   here is already final.
3. **Tiled 4K with RoPE offsets.** The deck shifts each tile's rotary ids into the parent frame
   before re-denoising. Upscaler nodes tile without that offset.
4. **Server-side adapter download, JSON API, clip stitching.** Deck features; the graph
   equivalents are the Manager, `/prompt`, and ffmpeg.

## Notes that save time

- `lora_name` lists only files present in `models/loras`; `None` disables a member without
  unwiring it.
- Adapters trained on the undistilled base still apply to the distilled core, so a publicly
  made adapter is usable here.
- Adapters from another architecture (the H3 line in the volume) load but render garbage; the
  deck refuses them by family, ComfyUI does not.
- `WanImageToVideo.length` must satisfy `4n+1`: 81 = 5 s at 16 fps, 121 = 5 s at 24 fps.
- If the core nodes report a missing class, update ComfyUI — support landed in mid-2026 builds.
