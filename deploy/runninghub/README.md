# RunningHub — managed ComfyUI workflow

Their GPUs, their idle time, billed per task. This is the only host in the list with **no idle
cost at all**, which is why it is worth the one-time upload.

## One-time setup

1. Create an API key (Console → API Key). A *Consumer-Member* key covers ComfyUI workflow tasks.
2. Open ComfyUI on RunningHub, load the Krea 2 graph (model files:
   `Comfy-Org/Krea-2` — `krea2_turbo_fp8_scaled`, `qwen3vl_4b_fp8_scaled`, `qwen_image_vae`,
   plus the style LoRAs), and **Download → Export Workflow API**.
3. Upload/save that workflow in RunningHub and copy its **workflowId**.
4. Open the exported json, find the node ids for the prompt text, the sampler seed, the empty
   latent size and the LoRA loader, and correct `node_map.json` if they differ from the
   defaults. RunningHub's UI flattens subgraphs, so the ids there are **not** the ones inside
   `comfy/_ref/krea2_t2i.json`'s subgraph — read them off your own export.

Then:

```bash
export RUNNINGHUB_API_KEY=...
export RUNNINGHUB_WORKFLOW_ID=...
python deploy/runninghub/submit.py --prompt "a woman in a rain-lit alley, black coat" \
  --quality --out out/runninghub.png
```

When that prints a saved PNG, the production rail takes it automatically: `orbit_engine.choose`
prefers RunningHub over Modal for every paid picture because $0.02/task beats ~$0.04 of H100
time, and the margin floor is checked before the order is routed anywhere.

## What the engine sends

`nodeInfoList`, one row per field, exactly as their "Start ComfyUI Task 2 — Advanced" documents
(`POST /task/openapi/create` with `apiKey` in the body, then `/task/openapi/status`, then
`/task/openapi/outputs`). No adapters are named by the client: `character` stays on this side,
because a request that could name its own LoRA could borrow another woman's face.
