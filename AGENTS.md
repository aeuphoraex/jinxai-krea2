# AGENTS.md — working rules for this repo

## Deployment discipline (read first)

**Do not restart or redeploy the Modal app unless a change genuinely requires it.** A restart
is not a verification tool, and it is not free: it costs a cold start, and because
`scaledown_window=600` the previous revision's containers can keep serving traffic for
minutes afterwards, which makes results look stale and invites a second, unnecessary deploy.

Rules that follow from that:

- **Batch first, deploy once.** Make every code change for a task, check it locally
  (`python -c "import ast; ast.parse(open('app.py').read())"`, pure-module unit checks), then
  deploy a single time.
- **Verify with read-only calls** after a deploy: `GET /health`, `GET /api/gallery`, `GET /`
  through the public domain. Do not re-run image/video renders "to be sure" — those are GPU
  minutes and were measured already.
- **Never `modal app stop` as a habit.** Only use it when containers are demonstrably serving
  a revision older than the deployed one (symptom: a route answers with the previous JSON
  shape minutes after a successful deploy).
- **No speculative deploys.** If the task is a question, a doc edit, or a local script, the
  deployed app does not change.
- Caddy is a separate process and gets the same treatment: restart it only when its config
  changed or it is wedged, never just to test reachability.

## Deploy

```bash
export MODAL_TOKEN_ID="..."; ./.venv/Scripts/modal.exe deploy app.py
```

App name `jinxai-orbital`, endpoint `https://xzyorb--orbital.us-east.modal.run`. The retired
`https://xzyorb--krea2.us-east.modal.run` label is claimed by the `krea2_alias` function,
which 307s everything to `https://dubwarz.vip/aigf` — without it Modal answers that host
with a bare `modal-http: invalid function call` 404.
Anything imported by `app.py` must be listed in `.add_local_python_source(...)` on **both**
`image` and `web_image`, or GPU/class containers die at startup while `/health` still answers.

## Locked render contract (`/aigf`)

The studio is prompt-only on purpose: AIGF Pro is sold as one button and a prompt. Everything
below is enforced **server-side** in `Krea2.generate` / `Krea2Video.generate`; the page hides
the inputs as UX, not as the control. Do not re-expose any of it as a request field, a
`<select>`, a slider or a query parameter — "just adding a little control back" breaks the
product and the price.

| Setting | Locked value | How |
| --- | --- | --- |
| steps / cfg | 8 / 0.0 (image), 24 / 5.0 (video) | pinned constants, request ignored |
| core + text encoder | `uncensored` + `abliterated` | `variant` / `encoder_kind` hard-set |
| prompt expander | always on | `_enhance` runs every render; no `enhance` field |
| per-image LoRA | always fitted | `want_lora = True`, response carries `lora_jobs` |
| style adapter | her profile adapter, then the admin's house pair | `orbit_studio.blend(...)`; `style_lora` / `style_loras` ignored outright |
| negative prompt | fixed by `orbit_guard.negative_for()` | the caller's value is discarded |
| size, strength, seed, count, frames, upscale | derived | request values are not read |

The page's only inputs are the prompt, the ratio/resolution chips and `character`
(`/aigf?c=<sku>`), which `/api/upsell/{sku}` also sends. A profile with no adapter renders
clean — never a near-miss borrowed from another woman's LoRA — and the page shows her
resolved adapter as read-only text (`#studioLoraNote`), never as a picker.

House adapters (currently `bimbo` + `breastshape`, so most renders wear them) are **admin
config, not code**: `studio.json` on the `krea2-weights` volume, default in
`STUDIO_HOUSE_DEFAULT`. Change it live with `GET`/`POST /api/studio/config` (the write is
`_guard`-ed) or `modal volume put krea2-weights studio.json ...` — no deploy needed. Return
`{"loras": []}` to render with her adapter alone.

An escort's own adapter is the same idea one level down: a `CUSTOM_LORAS` entry whose file
lives in `loras/` on the volume (Sophia's is `sophia_krea2.safetensors`, marked `local: True`,
so there is no URL and nothing for `--lora-all` to fetch). The catalog's `"adapter"` field names
it, `orbit_studio.pick()` resolves the profile sku to that key, and `_lora_catalog()` scans the
folder on every read — so *putting the file in is the deploy*. Until it is there the entry
reports `available: false`, `pick()` refuses it, and she renders with the house pair rather
than borrowing another woman's weights.

How Sophia's file was made (repeat this for the next escort): render her profile once
(`POST /api/generate` with `character: "dw-sophia"`), take the fit id from `lora_jobs`, wait
for `GET /api/lora/<job>` → `status: ready`, then promote that archive to her stable name:
`modal run app.py --lora-download=sophia --lora-url=file:///weights/loras/<stored>`. The fit is
fitted to her own frame, so it carries her face, not a style.

## Current state (2026-11-15)

- `dubwarz.vip` → local Caddy (`tools/caddy/caddy.exe`, config `tools/caddy/run_config.json`).
  Root is the main index on `127.0.0.1:8792`; the ORBITAL (krea2) backend is only under
  `/aigf/` (page at `/aigf`, gallery at `/aigf/gallery`, API at `/aigf/api/*` stripped back
  to `/api/*`) → Modal endpoint. The studio page's header links to that gallery, so keep
  `/aigf/gallery` served whenever `/aigf` is.
  Apply routing edits with `python tools/caddy_route_aigf.py --apply` (admin API, no
  restart); if Caddy itself must restart that needs elevation:
  `tools/caddy/restart_caddy.bat`. The process runs in the user session, not as a service.
- Gallery is global: local catalog + distilled characters (`orbit_girls.py`, Dict
  `orbital-girlfriends`) + peer catalogs (`?scope=local` is the peer-facing view).
- Chat: `POST /api/chat/{sku}` (female-only, `orbit_chat.py`) remembers each visitor in Dict
  `orbital-chats`, with `POST /api/profile/{uid}` letting the VLM read a profile picture.
  Both run on the unfiltered Qwen3-VL via `Krea2.talk`; the image tier needs torchvision,
  which the GPU image installs beside torch, or `AutoProcessor` fails at `AutoVideoProcessor`.
- Escort upsell: `GET/POST /api/upsell/{sku}` (`orbit_upsell.py`, Dict `orbital-upsell`)
  turns a roster photo into 1K thumb / 2K zoom / 4K download frames, VLM-recognised then
  img2img'd; the 1K frame is stored as her gallery thumbnail. `tools/upsell_escort.py` drives it.
- Stripe is unconfigured (`/api/shop/checkout` → 503 by design); `MARKET_SECRET` unset, so
  marketplace listings are unsigned.
