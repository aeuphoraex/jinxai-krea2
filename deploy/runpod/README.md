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

Console → Serverless → New Endpoint → your image, GPU **24 GB or larger**, container disk ≥ 20 GB,
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

## Add the repo to RunPod Serverless

The repository is a complete build context: the root `Dockerfile` is the worker and
`deploy/runpod/handler.py` is its entrypoint.

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

