# Human test script

Everything is deployed at `https://xzyorb--orbital.us-east.modal.run`. Nothing below needs a
Modal account — only a browser and one Civitai API token.

Run `./.venv/Scripts/python.exe preflight.py` first (read-only, no weights, no GPU render).
It should print `ready:` and exit 0. `/api/info` is served by the image worker, so a cold
container can make it sit for a minute before printing.

## 0. Give the server its token (once)

Civitai serves these NSFW files only to a logged-in request. The token is stored in the
volume at `loras/.civitai_token` (0600) and never returned by any endpoint.

- Browser: Custom LoRA panel → **Civitai API token (once)** → paste → **Save**
  → it should say `token stored (N chars)`.
- Or CLI:
  ```bash
  curl -X POST "$URL/api/lora/token" -H 'content-type: application/json' \
    -d '{"token": "<civitai.com → account settings → API keys>"}'
  ```

Then press **Download all** (or `POST /api/lora/download_all`). That fetches 13 image
LoRAs (~3 GB) and 8 video LoRAs (~2.1 GB); expect a few minutes and a per-name summary in
the panel. Re-run `preflight.py` when it finishes: the `weights in the volume` lines should
list every name.

## 1. Image LoRAs (Text → Image)

For each, pick the entry in **Custom LoRA**, leave the strength at its default, and use a
prompt that gives the LoRA room to show. Two renders per entry (one with the LoRA, one with
`none`, same seed) is the honest comparison.

| LoRA | default | prompt to start from | what to look for |
|---|---|---|---|
| `michelle` | 1.0 | `michelle, a woman on a rooftop at dusk, 35mm film` | a consistent face/identity across seeds |
| `breastshape` | 1.0 | `a portrait of a woman in soft window light` (trigger `mybreastsA` auto-added) | shape/volume change, no smearing at 0.8–1.3 |
| `bimbo` | 1.0 | `a selfie of a woman at a cafe, phone camera` (trigger `bmbo` auto-added) | exaggerated proportions, glossy lips, makeup |
| `titjob` | 1.0 | `a woman kneeling in front of a man, seen from the front` (trigger `titjob`) | pose dependency, front-on / POV works, side view degrades |
| `liza` | 1.0 | `liza, street portrait, autumn light` | copper hair, two braids, same face at new angles |
| `annai` | 1.0 | `annai, sitting on a windowsill, morning light` | identity holds when framing changes |
| `lily` | 1.0 | `lily, close-up portrait, soft rim light` | dark eyes, winged eyeliner |
| `estelle` | 1.0 | `estelle, editorial portrait, grey backdrop` | photoreal identity, pose stays prompt-driven |
| `natalie` | 1.0 | `natalie, freckles visible, golden hour` | freckles survive small sizes; face consistency |
| `rachelle` | 1.0 | `rachelle, natural window light, calm` | soft features, blue-grey eyes |
| `eyes` | **0.6** | `portrait of a woman, detailed irises` (trigger `j2st-3y3s`) | eye detail at 0.4/0.6/0.8 — overshoot above 0.8 |
| `laundromat` | 1.0 | `a woman by washing machines at night` (trigger `laundryafterdark`) | fluorescent night palette, cinematic framing |
| `steph` | 1.0 | `steph, casual portrait, daylight` | freckles + skin texture, identity across hairstyles |

Checks that matter: the response/status line must show `lora <name> @ <scale> (N layers)`
with `N > 0`; the same seed with `none` must look like the base model; switching between two
LoRAs in one session must not leak the first one's look.

## 2. Video LoRA (Video tab, I2V-A14B only)

Pick an init frame (drop or paste), **Video model = Wan 2.2 I2V-A14B**, then choose a video
LoRA. These four are the ones that can load:

| LoRA | prompt | trigger added automatically |
|---|---|---|
| `throat-a14b` | `she looks up at him, steady eye contact` | long trained sentence |
| `throat-low` | same | none |
| `bounce-a14b` | `she leans back, hands on hips` | `her breasts are bouncing` |
| `bounce-low` | same | `her breasts are bouncing` |

Expected: the clip keeps the init frame's subject, the motion follows the trigger, and the
status line reports `lora`, `lora_scale`, `lora_base` in the response (visible in the API
JSON; the UI shows the model line).

Negative tests — these must fail fast with a 400, not burn a render:

- `bounce-h3`, `throat-h3`, `cumshot-h3`, `blowjob-h3` → *"trained for MiniMax H3, but this
  request uses wan22-a14b"*. Those four models publish only H3 (or H3/LTX) versions; there
  is no Wan build, so nothing here can load them.
- `bounce-a14b` with **Video model = TI2V-5B** → *"trained for Wan Video 2.2 I2V-A14B, but
  this request uses wan22-5b"*. The Wan LoRAs are A14B-only.
- Any name that is not in the registry → *"unknown video LoRA … see /api/info video_loras"*.

## 3. Things that are expected, not bugs

- `POST /api/lora/download` without a token → 400 telling you to paste one. It never writes
  the Civitai login page into `loras/`; a bad token is caught by the safetensors header check.
- A cold container makes the first image ~40 s and the first A14B clip ~2 min (126 GB of
  expert weights); later requests are far faster.
- `/api/info` blocks on the image worker, so it can take a minute right after a deploy.
- The endpoint is public and unauthenticated: anyone with the URL can trigger downloads or
  replace the stored token (they cannot read it back).

## 4. Reporting

For each failure, the useful facts are: the LoRA name, `style_scale`/`lora_scale`, the exact
seed, whether the response reported a non-zero adapter/layer count, and the image or clip.
That distinguishes "the LoRA did nothing" (load path) from "the LoRA is too strong"
(scale) from "the model itself is wrong" (base mismatch).
