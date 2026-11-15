# Krea 2 Turbo · uncensored · on Modal

A self-hosted, unfiltered Krea 2 Turbo image generator running on Modal GPUs, with a
browser UI and a JSON API.

| piece | what it is |
| --- | --- |
| DiT | **Krea 2 Turbo** — 12B single-stream MMDiT, 8-step distilled, native 1K–2K |
| DiT (default) | **krea2-turbo-uncensored v1.1** — community merge, same tensor layout (fp8→bf16 dequantized on load) |
| Text encoder | **Qwen3-VL-4B-Instruct**, layers 2–35 fused (stock or **Krea2-abliterated**) |
| VAE | Qwen-Image f8 / 16-channel (encode **and** decode — img2img needs both) |
| Video | **Wan 2.2 I2V-A14B** (28B MoE, best realism) or **TI2V-5B** (fast) — image-to-video, 480p/720p, 2–5 s clips, own GPU worker |
| GPU | H100 by default (A100-80GB / L40S via env) |

Both DiT variants and both encoders are selectable in the UI, so you can A/B the
uncensored merge against the official checkpoint on the same seed.

## Deploy

```bash
python -m venv .venv && ./.venv/Scripts/pip install modal
export MODAL_TOKEN_ID=ak-...          # or: modal token set

modal deploy app.py                    # builds the image (~4 min first time)
modal run app.py::main --download-first # pulls ~65 GB of weights, warms the worker, renders one test image
modal run app.py::main --video-download              # both video pipelines (34 GB + 126 GB)
modal run app.py::main --video-download wan22-a14b   # just A14B, e.g. to skip the 5B
```

`--download-first` is a one-time step; it writes into the `krea2-weights` and
`krea2-hf-cache` volumes and validates that the DiT file has exactly the official
tensor names before the GPU ever sees it.

The deploy prints an endpoint like
`https://<workspace>--jinxai-orbital-krea2.modal.run` — open it for the UI, or
call the API below.

**This deployment:** <https://xzyorb--orbital.us-east.modal.run>

## Run the same code on RunPod Serverless

This repository is also the source for the **public** RunPod worker: stock Krea 2 Turbo, no
uncensored merge, no abliterated encoder, no adapters. It is built from the root `Dockerfile`
and served by `handler.py`, which answers in the same shape this app returns, so
`orbit_engine` can route an order to whichever host is cheaper without a branch.

```text
push to main  →  .github/workflows/runpod-image.yml  →  ghcr.io/<owner>/<repo>:latest
                                                       ↓
                 RunPod template + endpoint  ←  deploy/runpod/publish.py --skip-build
```

1. Push to `main`. The workflow builds the image; no local Docker daemon or registry login
   is needed.
2. Make the GHCR **package public** (Packages → the package → Settings → Change visibility)
   so RunPod can pull it without a stored credential.
3. Put the three weight files on a RunPod network volume and create the endpoint:

   ```bash
   python deploy/runpod/publish.py --skip-build \
     --image ghcr.io/<owner>/<repo>:latest --volume-id <network-volume-id> --apply
   ```

   The fp8 checkpoint is fine as-is: `deploy/runpod/checkpoint.py` dequantises it to bf16 once,
   beside the source, because `krea2.inference._pipeline` loads with `strict=True` and would
   otherwise fail on the per-tensor `_scale` entries.
4. Confirm one job end to end: `python deploy/runpod/selftest.py --live --endpoint <id>`.

Weights and adapters are never committed (`.gitignore`), and the studio deck is not in this
image — see `launch/distro_profit.md` for why the two lines are separate products.

Verified on H100: worker load **18 s**, 1024² at 8 steps **25 s**, full cold
request (boot + load + first compile) **43 s**, returned `HTTP 200` with a
1.2 MB PNG. At 2K: 2048² in **22 s** (5.8 MB PNG) and 2048×1152 in **11 s** warm.
Volume storage in use: ~90 GB (2 DiTs incl. the materialized bf16 merge,
2 encoders, VAE).

## API

```bash
curl -X POST "$URL/api/generate" -H 'content-type: application/json' -d '{
  "prompt": "extreme close-up of a woman's face partially obscured by tousled dark brown hair, cinematic warm lighting, film grain",
  "width": 1328, "height": 1328, "steps": 8, "cfg": 0,
  "num_images": 1, "seed": -1,
  "variant": "uncensored", "text_encoder": "abliterated",
  "enhance": false
}' | jq -r '.images[0].data' | base64 -d > out.png
```

Response: `images[] {data (base64 png), seed, width, height, seconds}`, plus `elapsed`,
`load_seconds`, `prompt` (post-expansion) and `enhanced`.

`GET /api/info` reports GPU, VRAM and which checkpoint is resident. `GET /health` is a liveness ping.

Cold start is ~90–150 s (24 GB of weights + first `torch.compile`). After that a
1024² 8-step image is a few seconds; 2048² is ~10–25 s depending on GPU.

## img2img

Same endpoint, two extra fields: `init_image` (base64 or a `data:` URL) and `strength`.

```bash
IMG=$(base64 -w0 frame.png)
curl -X POST "$URL/api/generate" -H 'content-type: application/json' -d "{
  \"prompt\": \"same scene, repainted in thick oil impasto\",
  \"init_image\": \"$IMG\", \"strength\": 0.55,
  \"width\": 1024, \"height\": 1024, \"steps\": 8, \"cfg\": 0
}" | jq -r '.images[0].data' | base64 -d > out.png
```

`strength` is the fraction of the schedule that gets re-run: 0.15–0.3 restyles while
holding composition and framing, 0.5–0.6 is a real reimagining, 0.85+ almost ignores
the frame. The image is resized to `width × height` and encoded through the same VAE
the sampler decodes with, then noised to `t = 1 - strength` and integrated from there;
the response carries `mode: "img2img"` and the strength actually used.

Verified: 1024², 8 steps, strength 0.55 → **10.4 s** render (12.4 s in-worker) on H100,
producing the same composition repainted rather than a new image.

## Per-image LoRA

Every image can emit a companion `.safetensors` LoRA. It cannot be extracted from the
render — inference changes no weights — so the image worker **spawns a second container**
(`Krea2LoRA`) that fits a rank-4 adapter on that image while the response is on its way
back. The image is returned immediately; the UI polls and turns the placeholder into a
download link.

The fit itself: the image is VAE-encoded at ≤512 px, re-noised at increasing timesteps
(0.2 → 0.9), and adapters on q/k/v/o, the attention gate and the MLP gate/up/down of all
28 blocks are trained for 40 AdamW steps to predict the flow-matching velocity under the
same text embedding the image was generated from. Rank 8 over 224 modules: ~44M parameters,
~88 MB fp32, PEFT keys under the `diffusion_model.` prefix, `ss_network_*` metadata carrying
prompt/seed/rank. `KREA2_LORA_RANK` and `KREA2_LORA_STEPS` override both if you want it
cheaper or stronger.

```bash
# spawn happens inside /api/generate; the response carries lora_jobs[]
curl "$URL/api/lora/<job-id>"        # {"status": "training" | "ready", bytes, loss_first, loss_last}
curl -o krea2.safetensors "$URL/api/lora/<job-id>/file"
```

`"lora": false` (or the UI checkbox) disables it. Each fit is its own GPU container, so
it roughly doubles the cost per image — leave it off while experimenting.

### Loading it outside this app

The file is keyed the way the working Civitai Krea 2 LoRAs are: `diffusion_model.blocks.N.
attn.wq.lora_A.weight` (plus `wv`, `wo`, `mlp.down`), fp32, rank 4, with `ss_network_module`,
`ss_network_dim`, `ss_network_alpha`, `ss_base_model_version` and `modelspec.title` in the
metadata. That is the layout ComfyUI and the hosted UIs read; a bare `blocks.N…` file is
valid safetensors but gets ignored, which is what SeaArt did with earlier exports.

Files produced before this change can be converted without touching the tensor data — only
the header is rewritten:

```bash
python port_lora.py krea2_seed777.safetensors -o krea2_seed777_portable.safetensors
```

Our own loader strips the prefix, so one file works here and abroad. Note the fit is thin
by design (rank 4, four module types, 24 steps): it captures the look of that one image,
not a general character, so expect a gentle effect rather than a trained model.

## Custom LoRA (optional)

A user-supplied `.safetensors` character or style LoRA can be applied to any render without
touching the base weights. It is a runtime adapter: every Linear the file names is wrapped
as `base(x) + scale · (x·Aᵀ)·Bᵀ`, so switching, rescaling or removing it needs no reload.

The registry (`CUSTOM_LORAS` in `app.py`) ships thirteen entries, all **off by default**:

| name | what | size | notes |
|---|---|---|---|
| `michelle` | character (Krea 2) | 446 MB | [models/2772050](https://civitai.com/models/2772050) |
| `breastshape` | concept, breast shape/volume | 229 MB | [models/3003699](https://civitai.com/models/3003699), trigger `mybreastsA`, author suggests 0.8–1.3 |
| `bimbo` | style, doll-like hourglass look | 218 MB | [models/3008606](https://civitai.com/models/3008606), trigger `bmbo`, strongest in phone-photo/selfie prompts |
| `titjob` | pose, titjob/paizuri | 218 MB | [models/2974641](https://civitai.com/models/2974641), trigger `titjob`, works best front-on or from his point of view |
| `liza` | character, copper-haired with two braids | 218 MB | [models/2987031](https://civitai.com/models/2987031), trigger `Liza` |
| `annai` | character | 224 MB | [models/2785776](https://civitai.com/models/2785776), trigger `Annai` |
| `lily` | character, dark eyes, winged eyeliner | 218 MB | [models/3030293](https://civitai.com/models/3030293), trigger `Lily` |
| `estelle` | character, photoreal identity | 218 MB | [models/2823738](https://civitai.com/models/2823738), trigger `Estelle` |
| `natalie` | character, freckles, gray-green eyes | 218 MB | [models/3013738](https://civitai.com/models/3013738), trigger `Natalie` |
| `rachelle` | character, blue-gray eyes, light brown hair (SFW-flagged, still login-gated) | 109 MB | [models/2982129](https://civitai.com/models/2982129), trigger `Rachelle` |
| `eyes` | style, soft expressive eyes (SFW flag) | 218 MB | [models/2978156](https://civitai.com/models/2978156), trigger `j2st-3y3s`, author suggests 0.4–0.8, default 0.6 |
| `laundromat` | concept, late-night laundromat fashion photography (SFW flag) | 218 MB | [models/2945185](https://civitai.com/models/2945185), trigger `laundryafterdark` |
| `steph` | character, freckles, natural skin texture (SFW flag) | 218 MB | [models/3017780](https://civitai.com/models/3017780), trigger `Steph-lds` |

### The server fetches the weights itself

Nothing has to be uploaded by hand. The first request that names a LoRA whose file is not
in the volume makes the worker download it, validate the safetensors header, commit it to
the volume and carry on with the render — the same helper backs `POST /api/lora/download`
(the **Download LoRA** button in the UI) and the CLI below.

Civitai answers an unauthenticated download with its HTML login page — verified for the
NSFW entries and for `rachelle` too, whose model page is not NSFW-flagged — so the server
needs a token before it can fetch any of them. **No key ships in the source.** Resolution
order is `CIVITAI_TOKEN` (secret or env), then `loras/.civitai_token` in the volume (0600,
never returned by any endpoint), then `CIVITAI_TOKEN_DEFAULT`, which is empty unless an
operator opts in at deploy time.

So every deployment brings its own key. Store it once — the **Save** button in the Custom
LoRA panel, `POST /api/lora/token`, or `modal run app.py --lora-token=<token>` — and it
lives in that workspace's volume. Set `ORBITAL_ACCESS_KEY` to require `X-Access-Key` or
`Authorization: Bearer` on the three mutating routes (`/api/lora/token`,
`/api/lora/download`, `/api/lora/download_all`); `GET /api/lora/token` reports whether a key
is configured without revealing it. Storing an empty string disables downloads until a key
is set again. Every download is otherwise entirely server-side:

```bash
curl -X POST "$URL/api/lora/token" -H 'content-type: application/json' \
  -d '{"token": "<civitai.com → account settings → API keys>"}'
# {"stored": true, "chars": 32}

curl -X POST "$URL/api/lora/download" -H 'content-type: application/json' \
  -d '{"name": "titjob"}'
# {"name": "titjob", "file": "Titjob_Krea2.safetensors", "mb": 218.0, "seconds": 41.2}

curl -X POST "$URL/api/lora/download_all"   # every registry LoRA, image and video
```

The image worker reloads the volume (throttled to once per 10 s) before it lists LoRAs, so
a file committed by a download container shows up in the next `/api/info` without
redeploying or restarting anything.

Without a token the download is refused before any bytes are written, with the exact
command to fix it. A rejected token is caught by the safetensors header check, so the
login page never lands in `loras/` pretending to be a model. CLI forms of the same calls:

```bash
modal run app.py --lora-download=michelle
modal run app.py --lora-download=breastshape
modal run app.py --lora-download=bimbo
modal run app.py --lora-download=titjob
modal run app.py --lora-download=liza
modal run app.py --lora-download=annai
modal run app.py --lora-download=lily
modal run app.py --lora-download=estelle
modal run app.py --lora-download=natalie
modal run app.py --lora-download=rachelle
modal run app.py --lora-download=eyes
modal run app.py --lora-download=laundromat
modal run app.py --lora-download=steph
# a URL for something not in the registry:
modal run app.py --lora-download=my_style --lora-url=https://example.com/my_style.safetensors
# or drop the file in yourself:
modal volume put krea2-weights michelle_krea2.safetensors loras/michelle_krea2.safetensors
modal volume put krea2-weights 2mybreastsA_Krea2_FINAL_LoRA.safetensors \
  loras/2mybreastsA_Krea2_FINAL_LoRA.safetensors
modal volume put krea2-weights bimboLoraForKrea2_v1.safetensors \
  loras/bimboLoraForKrea2_v1.safetensors
modal volume put krea2-weights Titjob_Krea2.safetensors loras/Titjob_Krea2.safetensors
modal volume put krea2-weights Liza_Krea2_V1.safetensors loras/Liza_Krea2_V1.safetensors
modal volume put krea2-weights Annai-Krea2.safetensors loras/Annai-Krea2.safetensors
modal volume put krea2-weights Lily_Krea2_V1.safetensors loras/Lily_Krea2_V1.safetensors
modal volume put krea2-weights lora_Estelle_000002500_Krea-2-Raw_rc117_v3.safetensors \
  loras/lora_Estelle_000002500_Krea-2-Raw_rc117_v3.safetensors
modal volume put krea2-weights Natalie_Krea2_V1.safetensors loras/Natalie_Krea2_V1.safetensors
modal volume put krea2-weights Rachelle_V1_CharacterLora_R4PT0R.safetensors \
  loras/Rachelle_V1_CharacterLora_R4PT0R.safetensors
modal volume put krea2-weights just-eyes-v3.2400.safetensors loras/just-eyes-v3.2400.safetensors
modal volume put krea2-weights Midnight_Laundromat___KREA2.safetensors \
  loras/Midnight_Laundromat___KREA2.safetensors
modal volume put krea2-weights lora_Steph-lds_Krea-2-Raw_rc210_v1.safetensors \
  loras/lora_Steph-lds_Krea-2-Raw_rc210_v1.safetensors

curl "$URL/api/info" | jq .custom_loras             # {name: {label, file, available, ...}}

# 2) use it
curl -X POST "$URL/api/generate" -H 'content-type: application/json' \
  -d '{"prompt": "michelle on a rooftop at dusk, 35mm film",
       "style_lora": "michelle", "style_scale": 0.8}'
```

Any `.safetensors` dropped into the volume's `loras/` folder is picked up under its
filename (`loras/my_style.safetensors` → `"style_lora": "my_style"`). Key prefixes from
diffusers, PEFT and ComfyUI are normalised, and `lora_alpha` is folded into the scale. A
registry entry may name a `trigger` word (`breastshape` → `mybreastsA`, `bimbo` → `bmbo`, `titjob` → `titjob`, `liza` → `Liza`, `annai` → `Annai`, `lily` → `Lily`, `estelle` → `Estelle`, `natalie` → `Natalie`, `rachelle` → `Rachelle`, `eyes` → `j2st-3y3s`, `laundromat` → `laundryafterdark`, `steph` → `Steph-lds`); it is prepended
to the prompt when it is not already there, so you can write `"a portrait in soft light"`
and still get the concept. The
response echoes `style_lora`, `style_scale`, `style_trigger`, `prompt_used` (the prompt that
was actually encoded) and `style_adapters` (how many layers were
wrapped). Omitting `style_scale` uses the entry's `default_scale` (1.0 unless the author
recommends otherwise), and picking an entry in the UI sets the slider to it.
`"style_lora": ""` removes the adapter again. Scale runs 0–2, where 1.0 is the
file's intended strength; 0.6–0.8 is usually kinder to anatomy. In the UI it is the
**Custom LoRA** dropdown, the strength slider and **Stack +**; entries whose file is not in
the volume read `(server will fetch)` and the worker downloads them on first use.

Several LoRAs can be combined. `style_loras` takes a list of objects, `"name:scale"`
strings, `[name, scale]` pairs, or one comma-separated string:

```bash
curl -X POST "$URL/api/generate" -H 'content-type: application/json' -d '{
  "prompt": "a woman in a cafe by the window",
  "style_loras": [{"name": "breastshape", "scale": 0.7}, "liza:0.6"],
  "enhance": true
}'
```

Each layer gets one wrapper that sums every stacked adapter, so removal restores the
original Linear exactly. The response echoes `style_loras` (what was applied, in order),
`style_adapters` (layers wrapped) and `style_triggers`. Every stack member's `trigger` is
added to the prompt — once before the expander runs and again afterwards, since the
expander rewrites the whole sentence and would otherwise drop the trained word. The first
entry is what `style_lora` / `style_scale` report, for older clients.

## 4K

Pass `upscale` with a target long side (e.g. `3840`). Generation still happens at 2K —
4096² is 22k tokens, outside both the compute budget and the training distribution —
then the image is Lanczos-upscaled and re-denoised as overlapping 1536 px tiles at
strength 0.32. Each tile is denoised with its true spatial offset (the RoPE ids are
shifted into the parent frame), so neighbours agree on global layout, and the 192 px
overlaps are cosine-feathered. Tiles are 1536 px, not 1024, to keep the tile count at 9
for a 3840 px image. Expect minutes, not seconds.

Measured on H100: 2048² → 3840² (3×3 tiles) is **45 s** of tiling on top of a 42 s base
render — **115 s** total, 17.8 MB PNG. Inspected at 100% over the tile boundaries, there
are no visible joins.

## Video

`POST /api/video` — image-to-video. An init frame is **required**: the clip starts on
it, so the frame carries the content and the prompt carries the motion. `model` picks
`wan22-a14b` (default, 28B MoE, best realism) or `wan22-5b` (one 5B transformer, ~3×
faster).

Each response also carries `last_frame` — the clip's final frame as a JPEG. That is the
chain link: send it back as `init_image` and the next clip continues this one.

## Video LoRA

Video LoRAs live in their own registry (`VIDEO_LORAS`) and their own folder,
`/weights/loras/video`, because they are trained against a Wan transformer and are loaded
by diffusers rather than by the image-side key matcher. They are applied through
`pipe.load_lora_weights()` + `set_adapters()`, unloaded when you switch, and downloaded by
the same server-side fetcher as the image LoRAs.

| name | what | base | file |
|---|---|---|---|
| `throat-a14b` | deepthroat / face fuck, v3.0 High | Wan 2.2 I2V-A14B | `Wan22_ThroatV3_High.safetensors` (293 MB) |
| `throat-low` | same motion, v3.0 Low (no trigger text) | Wan 2.2 I2V-A14B | `Wan22_ThroatV3_Low.safetensors` (293 MB) |
| `throat-h3` | v0.1, trained on MiniMax H3 | *MiniMax H3* | `MMH3_AllInOne-000004.comfy.safetensors` (284 MB) |
| `bounce-a14b` | bouncing breasts, High | Wan 2.2 I2V-A14B | `BounceHighWan2_2.safetensors` (293 MB) |
| `bounce-low` | bouncing breasts, Low | Wan 2.2 I2V-A14B | `BounceLowWan2_2.safetensors` (293 MB) |
| `bounce-h3` | FL2VA v0.7, trained on MiniMax H3 | *MiniMax H3* | `bounceV07_fl2va-000230_Intense.safetensors` (569 MB) |
| `cumshot-h3` | H3 ALPHA, trained on MiniMax H3 | *MiniMax H3* | `epic_cumshots-MiniMaxH3-ALPHA-CUMSH0T.safetensors` (148 MB) |
| `blowjob-h3` | v1.0, trained on MiniMax H3 | *MiniMax H3* | `MessyBlowjob_v1_H3.safetensors` (296 MB) |

The `throat-*` entries come from [models/2023407](https://civitai.com/models/2023407), the
`bounce-*` ones from [models/1343431](https://civitai.com/models/1343431) (that page also
ships LTX versions, which are not registered). `cumshot-h3` comes from
[models/2621242](https://civitai.com/models/2621242), which publishes only MiniMax H3 and
LTX 2.3 versions, and `blowjob-h3` from [models/2997232](https://civitai.com/models/2997232),
which has a single H3 version — neither has a Wan build, so both can be fetched but never
applied here. Only the Wan entries run here: this app
serves Wan 2.2, while the H3 versions are kept so their files can be fetched for a MiniMax
pipeline — the Wan workers refuse them with a 400 naming both bases instead of loading
weights that would match nothing.

```jsonc
{"prompt": "she looks up at him, steady eye contact", "model": "wan22-a14b",
 "lora": "throat-a14b", "lora_scale": 1.0, "init_image": "data:image/jpeg;base64,…"}
```

The Wan entries carry their trained trigger sentence, which is prepended to your prompt if
it is missing — the motion LoRAs are prompt-conditioned, so the text matters as much as
the weights. The response echoes `lora`, `lora_scale` and `lora_base`. In the UI it is the
**Video LoRA** dropdown in the Video panel, with entries that need another base marked `!`.

## Stitching

The UI keeps a clip timeline: render, **Extend last** (feeds `last_frame` back in as the
next init frame, so clips continue rather than cut), remove, and **Stitch** to one file.

The endpoint behind it is `POST /api/video/stitch` with `{"clips": [base64, ...], "fps": 24}`.
It has no GPU: a CPU worker normalises every clip to the largest size with letterbox
padding, re-encodes at CRF 17, then concatenates. Mixed 480p/720p clips therefore join
cleanly instead of failing on the concat demuxer.

```bash
curl -X POST "$URL/api/video/stitch" -H 'content-type: application/json' \
  -d '{"clips": ["<b64>", "<b64>"], "fps": 24}' | jq -r '.video' | base64 -d > reel.mp4
```

```bash
IMG=$(base64 -w0 frame.png)
curl -X POST "$URL/api/video" -H 'content-type: application/json' -d "{
  \"prompt\": \"she turns toward the window as the late light shifts, handheld camera\",
  \"init_image\": \"$IMG\", \"frames\": 81, \"quality\": 480,
  \"steps\": 24, \"cfg\": 5, \"seed\": -1
}" | jq -r '.video' | base64 -d > clip.mp4
```

* `frames` must be `4k+1`: 49 / 81 / 121 ≈ 2.0 / 3.4 / 5.0 s at 24 fps.
* `quality` is 480 or 720 (long side 832 / 1280, total pixels capped at 1280×704).
* The response returns `video` (mp4 base64) plus `thumb` (JPEG poster), dimensions,
  fps, seed and render seconds.

**A14B memory.** Its two experts are 57 GB each on disk (fp32), 126 GB with the text
encoder — impossible on one 80 GB card. `enable_model_cpu_offload()` owns placement: one
28 GB bf16 expert sits on the card while the other waits on the host, and accelerate
moves the next up at the `boundary_ratio 0.9` boundary.

Do **not** hand-place the components instead. diffusers resolves `_execution_device`
from `pipe.device`, which prefers a non-CPU component; once the text encoder is parked
on CPU the pipeline decides it runs on CPU and dies building latents with *"cannot
generate a cpu tensor from a generator of type cuda"*. Host RAM also has to be raised
(`memory=196608`): the default 64 GB gets the container killed part-way through loading
126 GB of shards.

Measured: 832×832, 33 frames, 12 steps → **113 s** render, 214 s in-worker, 376 s wall
including a 70–110 s load and the expert swap. 720p is capped at 49 frames.

Wan 2.2 is not a distilled checkpoint: 20–30 steps at CFG ≈ 5 is the useful range.
Measured on H100: 832×832, 81 frames, 24 steps → **39 s** render, 0.55 MB mp4 (97 s
including a cold pipeline load); 49 frames at 8 steps → 18 s, but 8 steps is visibly
soft for video — that setting is for smoke tests only. 720p is ~2× the pixels.

## Tuning

| env | default | meaning |
| --- | --- | --- |
| `KREA2_GPU` | `H100` | `A100-80GB` roughly halves the hourly cost, ~2–3× slower |
| `KREA2_SCALEDOWN` | `300` | idle seconds before the image worker stops (lower = cheaper) |
| `KREA2_VIDEO_GPU` | same as `KREA2_GPU` | GPU for the video worker |
| `KREA2_VIDEO_SCALEDOWN` | `180` | idle seconds before the video worker stops |
| `KREA2_VIDEO_REPO` | `Wan-AI/Wan2.2-TI2V-5B-Diffusers` | swap in a finetune without touching the code |

Re-deploy after changing: `KREA2_GPU=A100-80GB modal deploy app.py`.

The GPU worker is capped at `max_containers=1` and `max_inputs=1`, so requests queue
instead of OOMing or spinning up a second paid GPU.

Video runs in a **separate container** (`Krea2Video`): Wan's 5B transformer plus the
fp32 VAE would otherwise compete with the 12B DiT for the same 80 GB. Consequence:
if you render an image and then a clip, both workers stay warm and you are paying for
two H100s until the idle windows expire — hence the short video window.

### 2K memory

The Qwen VAE decodes the whole frame in one pass; at 2048² its causal conv3d stack asks
cuDNN for a **~20 GiB workspace**, which does not fit next to a resident 12B DiT +
text encoder. The worker therefore:

1. parks the 8.5 GB text encoder on CPU for every decode,
2. above ~1792 px, also parks the 24 GB DiT (reloaded after the last image of a batch),
3. sets `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` in-process before torch
   initialises, and purges the allocator before decoding.

Cost is ~2–4 s of PCIe transfer per high-res image; 2048 is the practical ceiling on
80 GB. Past that the worker raises an explicit `RuntimeError` instead of thrashing.

## ComfyUI

`comfy/` holds stock-ComfyUI graphs that reproduce both generation paths: Krea 2 Turbo
t2i + img2img with a stacked-LoRA chain, and Wan 2.2 I2V-A14B with the high/low expert
pair and a Wan LoRA on each. They use the node classes from the official Comfy-Org
templates (8 steps / CFG 1 / euler+simple, `CLIPLoader type=krea2`, shift 5.0), so no
custom nodes are needed.

```bash
python comfy/build_workflows.py            # regenerate the two JSON files
python comfy/build_workflows.py --check    # slot/link/class validation
```

Drag `comfy/orbital_portrait.json` or `comfy/orbital_motion.json` onto the
canvas. Per-image LoRA fitting, trigger-aware prompt expansion and the RoPE-offset 4K tile
refine have no graph equivalent — `comfy/README.md` says why and what to use instead.

## Launching on SeaArt

`launch/` is the go-to-market pack: an app spec (base model, sampler, aspect presets, LoRA
stack, inputs, prompt template), the listing copy, and the economics.

```bash
python launch/check_app_spec.py    # sampler drift and NSFW-in-stack guard
```

`launch/modal_release.md` covers releasing this endpoint itself: proxy-auth gating, the
missing browser-side key, the `spawn`-based batch workflow, and the $7.90/hr abuse ceiling
that comes from `max_containers=1`. Three more cover the network side: `launch/network.md`
(peer table, gossip, least-loaded forwarding, usage ledger, Stripe destination charges),
`launch/marketplace.md` (signed adapter listings with rendered thumbnails) and
`launch/dubwarz.md` (the `/gallery` page, domain wiring, and the processor reality for adult
SKUs).

SeaArt's Creator Incentive Program pays on Original Models, AI Apps and AI Characters that
are original and positively oriented, so the published app is the **SFW** Krea 2 Turbo
product — the uncensored stack, prompt expansion and per-image LoRA fitting stay on this
server as the private/API path. `launch/README.md` has the checklist and says exactly what
is still missing (a SFW LoRA choice, showcase images, a confirmed SeaArt node set).

## Testing the LoRAs by hand

`TESTING.md` is the human test script: one prompt per image LoRA, the four video LoRAs that
can load, the four that must be refused, and what counts as a real failure. `preflight.py`
is the read-only check that runs first — no weights, no render:

```bash
./.venv/Scripts/python.exe preflight.py            # exit 0 = ready to test
```

It verifies `/health`, both catalogues (13 image + 8 video entries), that the Wan entries
are tagged `wan22-a14b` and the H3 ones tagged incompatible, that the unknown-name and
MiniMax refusals really return 400, and that the UI ships the LoRA controls.

## Notes

Three quirks of this stack are handled in code, in case you change checkpoints:

* The community merge ships `fp8_scaled` weights (per-tensor `weight_scale`), so
  `download()` dequantizes it to a plain bf16 file once — otherwise every cold start
  pays ~2 min of CPU conversion.
* The Krea2-abliterated encoder's `config.json` omits `rope_scaling`; the worker merges
  the stock checkpoint's `mrope_section` in before construction.
* Its `tokenizer_config.json` stores `extra_special_tokens` as a list, which transformers
  4.57 rejects, so the byte-identical stock tokenizer is used for it.
* Modal containers are pinned to the volume snapshot from their start, so the worker
  calls `Volume.reload()` before loading weights.

* The web endpoint is publicly reachable by default. If you want it private, add
  `requires_proxy_auth=True` to the `@modal.asgi_app()` decorator and use a Modal
  proxy token, or front it with a Cloudflare Access policy.
* Image sizes are snapped to multiples of 16 (8× VAE compression × 2× patch).
* `mu` is pinned to 1.15, the resolution shift the distilled checkpoint was trained at.
* The prompt expander uses Krea's own `docs/expansion.txt` system prompt against the
  already-resident Qwen3-VL, so it costs no extra download or GPU.
* Weights: Krea 2 is under the [Krea 2 Community License](https://www.krea.ai/krea-2-licensing)
  (permissive; >50 seats requires an enterprise license). The license also obliges
  operators to prevent illegal material — this deployment applies no output filtering,
  so that obligation is yours. Don't use it for CSAM, non-consensual intimate imagery,
  or defamation.
