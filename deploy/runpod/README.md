# RunPod — serverless worker (scale to zero)

RunPod bills per second and scales to zero, which makes it the cheapest *GPU* host for sporadic
orders and the worst for a steady queue (every cold worker reloads 12 GB of weights). The engine
still routes by estimated cost, so it is chosen only when it wins on paper.

## What gets deployed

`handler.py` is a standard RunPod serverless handler. It loads the same three components the
Modal worker loads — MMDiT, Qwen3-VL conditioner, Qwen VAE — from the `krea2/` package already in
this repo, and answers in this deployment's own shape:

```json
{"images": [{"data": "data:image/png;base64,…", "seed": 7, "width": 1280, "height": 1600,
             "seconds": 31.2}], "steps": 8, "engine": "runpod"}
```

That shape is the contract `orbit_engine.render` normalises and `commission_render` consumes, so
the order record needs no branch for "who drew it".

## Build and push

```bash
docker build -f deploy/runpod/Dockerfile -t ghcr.io/<you>/velvet-runpod:1 .
docker push ghcr.io/<you>/velvet-runpod:1
```

Weights are **not** baked into the image: the image is ~8 GB of CUDA and Python, and the 17 GB of
model files come from a RunPod network volume mounted at `/runpod-volume`. Upload them once:

```
/runpod-volume/weights/krea2_turbo_fp8_scaled.safetensors
/runpod-volume/weights/qwen3vl_4b_fp8_scaled.safetensors
/runpod-volume/weights/qwen_image_vae.safetensors
```

## Create the endpoint

Console → Serverless → New Endpoint → your image, GPU **32 GB or larger**, container disk ≥ 60 GB,
network volume attached, env:

| Variable | Value |
|---|---|
| `OSS_TURBO` | `/runpod-volume/weights/krea2_turbo_fp8_scaled.safetensors` |
| `HF_HOME` | `/runpod-volume/hf` (the Qwen encoder is pulled once and cached) |

Then on this machine:

```bash
export RUNPOD_API_KEY=...
export RUNPOD_ENDPOINT_ID=...
python -c "import orbit_engine as E; c=E.RunPod(); print(c.wait(c.create({'prompt':'a portrait','width':1280,'height':1600,'seed':7})))"
```

## Capacity: `allowedCudaVersions` is an exact match, per host

`GET /v2/catalog/gpus?include=AVAILABILITY&product=SERVERLESS` (Bearer key) returns every GPU
with an `availability` and a `cudaVersions` array whose entries carry their own `available` flag.
On 2026-11-16 the A40 read:

```json
"availability": "MEDIUM",
"cudaVersions": [{"version": "12.8", "available": false},
                 {"version": "13.2", "available": true}]
```

RunPod matches the requested list **exactly** against the version a host reports. A list of
`12.6 … 13.0` therefore matched no A40 at all, and the Hub answered "no capacity on A40" while
the pool was MEDIUM — no amount of extra GPU models would have helped. `13.2` is now first in
`.runpod/hub.json`, `.runpod/tests.json` and `publish.CUDA_VERSIONS`, and `selftest.py` fails if
the three disagree. The rest of the list stays: those drivers can still start the image.

Two rules follow, and the old list broke the second one:

- Every entry must be **at or above the image floor**. Both Dockerfiles use
  `nvidia/cuda:12.6.3`, which declares `cuda>=12.6`; a 12.4 entry schedules onto a host whose
  container then dies at prestart, which is worse than no capacity.
- The list must be **wide and re-read from the catalog** whenever a launch has no capacity. Never
  add a version no machine reports, and never assume the newest published enum is the newest
  deployed driver. `product=POD` is what `tests.json` schedules on; `product=SERVERLESS` is what
the listing deploys.

Also current as of the same read: `NVIDIA RTX A6000` and `NVIDIA L40` had **no** availability in
either product. They remain inside `AMPERE_48` / `ADA_48_PRO`, so the pools keep them as future
targets, but a test pinned to one named device has to name a device that is up — today the A40,
the L40S or the RTX 6000 Ada, all at 13.2.

## 32 GB cards: the encoder is parked, not co-resident

Measured from the published files' own headers: the Turbo DiT loads at **26.28 GB** (25.0 GB bf16
plus 1.29 GB float32 scalars), the Qwen-Image VAE at 0.25 GB and Qwen3-VL-4B at 8.88 GB — 34.3 GB
of weights before a single activation. `krea2.inference._pipeline` moves all three to the GPU, so
the smallest card that could run a job was a 48 GB one, and `hub.json` was advertising `AMPERE_24`
and `ADA_24` pools that die in `_pipeline` on the first job: the DiT alone is larger than the card.
No arrangement of the encoder fixes that, which is why those pools are gone rather than tuned, and
what `tests.json` pins an A40 for instead.

`lowvram.py` decides from the free VRAM left after the loader has run:

- **`resident`** — a 48 GB card holds both, so nothing moves. Byte for byte the old behaviour.
- **`streamed`** — the encoder is parked on the CPU and one decoder layer at a time is moved
  through the GPU for the length of one prompt encode. The peak becomes the renderer plus the
  encoder's small non-layer modules (`embed_tokens` 0.78 GB, visual tower 0.83 GB) and a single
  201.9 MB layer, instead of all 8.88 GB at once. It costs 8.88 GB of PCIe traffic per prompt
  (~0.4 s at PCIe 4.0 x16), and only while the encoder runs. The 36-layer stack is found by shape
  — the largest run of same-class children — so this does not depend on a `transformers`
  attribute name that a version bump can rename.

The render is unchanged: `sampling.sample` already takes pre-computed conditioning (`text=`), so
the handler encodes first and hands it to the sampler. `selftest.py` checks the strategy
thresholds, the layer discovery, that a layer is on the card for its own forward and off it
afterwards, that no hook survives the encode, that a failed encode still parks the encoder, and
that every pool `hub.json` advertises holds `pool_floor_bytes()` (28.5 GB) — which is 32 GB and up.

The streamed path is proven offline; one live job on a 32 GB endpoint is what proves it on a card.

## Cold start is the whole cost argument

Measured on this pipeline: 92 s cold vs 33.6 s warm on an H100, of which ~58 s is weight load.
RunPod's `estimated_cost` in the engine adds 45 s of the same load to every job, so a burst of
orders looks expensive there and a single order looks cheap — which is exactly the behaviour
`launch/economics.md` §1 describes for an idle GPU.

## The public line, and the fp8 checkpoint

This worker is the **public** product: stock Krea 2 Turbo, no uncensored merge, no abliterated
encoder, no adapters. That is what makes it safe to sit behind a Hugging Face Space, and it is
why the Space calls this instead of `/aigf/api/generate`.

`krea2.inference._pipeline` loads the DiT with `strict=True`, so a ComfyUI `fp8_scaled` file
carrying `<name>_scale` entries fails the job. `checkpoint.py` applies the same transform as
`app.py::_dequant` — bf16 weight times bf16 scale — and caches the result beside the source as
`<name>.bf16.safetensors`, so the ~12 GB rewrite is paid once per volume, not per cold worker.
Leaving `OSS_TURBO` pointing at the fp8 file is correct; the worker converts it on first load.

## What the Hub actually sends, and what it will accept

A test pod on 2026-11-16 booted clean on an A40 at CUDA 13.2, passed every fitness check, then
failed **both** tests with `prompt is required` and `jobStatus=FAILED`. Two mistakes, both ours,
neither visible to an offline test that stopped at the handler's own signature:

- **`tests.json` `input` is the raw job payload.** The Hub wraps it in the submission envelope
  (`{"input": <your input>}`), exactly as `/run` does. Writing `{"input": {"input": {…}}}` in
  the file makes the handler receive `{"input": {…}}` as its `input`, so `prompt` is empty and
  the job dies before the guard. Every test here is now a flat payload, and `selftest.py` feeds
  each one through the handler, so this cannot return without failing offline.
- **A refusal must be a completed job.** RunPod marks any job whose output carries an `error` key
  as `FAILED` (`error_class=worker_app_error`), which means a refusal returned that way can never
  pass the `refusal costs no GPU time` test. The worker now answers a policy refusal with
  `{"refused": <reason>, "images": []}` and no `error`, so the job completes, the reason is still
  explicit, and nothing touched the GPU. `orbit_engine.RunPod.wait` and the Space both read
  `refused` before treating an empty `images` list as a bug.
- **A repo id must name its file.** `krea/Krea-2-Turbo` publishes six safetensors, and
  `checkpoint.fetch` refuses to guess, so the test env and the Hub default are
  `krea/Krea-2-Turbo::turbo.safetensors`. A bare repo id would have been the next failure.

- **The default checkpoint must download without credentials.** A test pod has no Hugging Face
  token, and `krea/Krea-2-Turbo` is gated (`"gated": "auto"`): the next test run reached the
  download and died with `GatedRepoError: 401 … You must have access to it and be authenticated`.
  The listing default is now `Comfy-Org/Krea-2::diffusion_models/krea2_turbo_bf16.safetensors` —
  the same Turbo DiT in the same ungated repo, which answers an anonymous range request. It is the
  **bf16** file rather than the fp8 one on purpose: `materialize` reads its safetensors header,
  sees no `_scale` entries and returns the file untouched, so a cold worker downloads 26.28 GB and
  loads it — instead of loading 13.1 GB of fp8, holding it and its bf16 conversion in host RAM at
  once (~39 GB peak) and writing a second 26 GB copy to disk before the DiT can be loaded at all.
  Note the header check, not the filename: this file is `krea2_turbo_bf16.safetensors`, which does
  not match the `.bf16.safetensors` suffix the cached dequant output uses. The encoder
  (`Qwen/Qwen3-VL-4B-Instruct`) and VAE (`Qwen/Qwen-Image`) were never gated; only Krea's own repo
  was. Anyone who wants those original weights instead sets `HF_TOKEN` (after accepting the licence
  on the model page) and can then point `OSS_TURBO` back at `krea/Krea-2-Turbo::turbo.safetensors`.

- **The runtime image has no C compiler.** The third run got past the download, loaded the weights
  and died on the first frame with `torch._inductor.exc.InductorError: RuntimeError: Failed to find
  C compiler`. `krea2/mmdit.py` decorates `RMSNorm`, `PositionalEncoding` and `LastLayer` with
  `@torch.compile(fullgraph=True)`, so the first forward hands a graph to Inductor, and Triton's
  driver compiles a C extension with `cc` before it emits a kernel — and the base is
  `nvidia/cuda:*-runtime`, which deliberately carries no toolchain. `handler.pipeline()` now calls
  `_no_dynamo()` before it imports `inference`: `torch.compile` becomes an identity decorator and
  `torch._dynamo.config.disable` is set, so a template's environment cannot put the compiled path
  back. Both Dockerfiles also export `TORCH_COMPILE_DISABLE=1`, the name
  `torch/_dynamo/config.py` actually reads. Installing gcc instead would have made every cold
  worker compile kernels for whichever GPU it landed on — a worse trade for a scale-to-zero
  endpoint, and eager is the reference arithmetic anyway.

`python deploy/runpod/selftest.py --remote` asks the pod's question from here — an anonymous
`Range: bytes=0-0` request for each configured source — so a gated or misspelt default fails
before a test run is spent on it.

The Hub counts a test as valid on a 200 response, which a `FAILED` job does not give you — so a
red test row is a real defect, not a formality.

## Publish

```bash
python deploy/runpod/publish.py          # prints the template + endpoint payloads, creates nothing
python deploy/runpod/publish.py --apply   --image ghcr.io/<you>/velvet-runpod:1 --volume-id <network-volume-id>
```

`publish.py` builds, pushes, reuses-or-creates the template by name, then creates the endpoint
and prints its id. It refuses to pretend: a local image tag, a missing volume id or a missing
key is reported as a blocker instead of a half-created endpoint.

Then confirm the worker answers — one job, one GPU-second:

```bash
python deploy/runpod/selftest.py --live --endpoint <id>
```

## Which files actually go on the network volume

Only the DiT is a file path the worker opens. `krea2.inference._pipeline` builds the
autoencoder and the Qwen3-VL conditioner from Hugging Face ids, and `transformers` caches them
into `HF_HOME` on first load:

```text
/runpod-volume/weights/krea2_turbo_fp8_scaled.safetensors   # required, read via OSS_TURBO
/runpod-volume/hf/                                          # encoder + VAE land here on first boot
```

The fp8 DiT is the right file to upload: `checkpoint.py` dequantises it to bf16 once, beside
the source, because `_pipeline` loads with `strict=True` and the fp8 file carries per-tensor
`_scale` entries that a strict load rejects. Pre-seeding `HF_HOME` with the encoder and VAE is
optional and only saves the first worker's download.

The Hub listing, which has no volume, names the published bf16 repack instead: it needs no
conversion, and a cold worker that reads fp8 and writes bf16 needs ~39 GB of host RAM for one job.

## Add the repo to RunPod Serverless

The repository is a complete build context: the root `Dockerfile` is the worker and
`handler.py` is its entrypoint.

**Route 1 — image (works today, no console integration).** Push to `main`; the
`runpod-image` workflow publishes `ghcr.io/<owner>/<repo>:latest`. Make that package public
(Packages → the package → Settings → Change visibility), then:

```bash
python deploy/runpod/publish.py --skip-build   --image ghcr.io/<owner>/<repo>:latest --volume-id <volume-id> --apply
```

**Route 2 — repository source.** If the console offers a GitHub source for a serverless build,
connect this repository (installing the RunPod GitHub app, since it is private), point the build
at `/Dockerfile`, then attach the network volume and set `OSS_TURBO` + `HF_HOME` in the template
environment. This is the same image Route 1 produces, built by RunPod instead of Actions.

Either route ends at the same endpoint. Confirm it with one job:

```bash
python deploy/runpod/selftest.py --live --endpoint <id>
```

