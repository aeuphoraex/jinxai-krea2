# MimicPC — a rented ComfyUI the engine can call

MimicPC is a ComfyUI you rent by the hour. It is the one host here that **bills while idle**, so
it is for batches, not for a storefront: start it, run the batch, stop the app.

| Hardware | Rate | 24 GB of VRAM holds |
|---|---|---|
| Large (L4/A10, 24 GB) | $1.29/h instant, $0.79/h bargain | Krea 2 fp8 (12.3 GB) + Qwen3-VL encoder (4.9 GB) + VAE, tight but workable |
| Ultra (L40S, 48 GB) | $2.59/h | comfortable, and the only tier that can also do Wan video |

## Session checklist

1. **Add New App → ComfyUI**, pick Large for pictures.
2. Open the app's terminal and run `bash deploy/mimicpc/bootstrap.sh`. It installs
   `cloudflared`, starts ComfyUI bound to loopback, checks the three model files, and prints a
   `https://<random>.trycloudflare.com` URL.
3. On this machine: `export MIMICPC_COMFY_URL=<that url>` and `export COMFY_WORKFLOW=<path to
   the API-format json>` — ComfyUI's *Save (API)* output, not the UI workflow.
4. `curl "$MIMICPC_COMFY_URL/system_stats"` must answer with JSON before the rail will use it.
5. Smoke test:

   ```bash
   python deploy/runninghub/submit.py --prompt "test portrait" --out out/mimic.png  # replace with the ComfyUI client
   ```

   The engine's own path is `orbit_engine.ComfyUI`: `POST /prompt`, poll `/history/<id>`, fetch
   `/view?filename=…`. Same three calls ComfyUI's documentation describes.
6. **Stop the app in the MimicPC dashboard** when the batch ends. The tunnel dies with the
   container; the meter does not.

## Why it is not the default

At $1.29/h a single picture is cheap, but every idle minute is charged. `orbit_engine` still
prices it per render, so the ledger reads the naive figure; the honest number includes the idle
hours, which is what `launch/economics.md` warns about for the Modal warm window too.
