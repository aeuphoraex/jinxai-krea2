"""Krea 2 Turbo on Modal.

Highest-quality open-weights text-to-image, uncensored:

  * DiT      : Krea 2 Turbo (12B single-stream MMDiT), official weights + an
               uncensored community merge, both in native key layout.
  * Encoder  : Qwen3-VL-4B-Instruct (stock) or the Krea2-specific abliterated
               variant, layer-fused exactly as the official repo does.
  * VAE      : Qwen-Image f8/16ch.

Deploy:   modal deploy app.py
Warm+test: modal run app.py::main --download-first
"""

from __future__ import annotations

import base64
import io
import os
import re
import time

import modal

# Must be set before the CUDA caching allocator is initialised (i.e. before torch is
# imported), otherwise the 2K VAE decode strands ~20 GiB as reserved-but-unallocated.
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

# Modal app listing name. Creative, not descriptive: the model names live in the weights,
# not on the dashboard. Function URLs come from the asgi label, so this rename does not
# change the public address.
APP_NAME = "jinxai-orbital"

app = modal.App(APP_NAME)

# Civitai gates these files behind a login, so the server needs one token. It is stored
# in the volume (not a Modal secret: a secret that does not exist yet fails the deploy),
# written by `store_lora_token` and read by `_lora_token` at download time.

# H100 is the sane default (bf16 12B + 2K latents). A100-80GB halves the cost/hour.
GPU = os.environ.get("KREA2_GPU", "H100")
# Idle GPU seconds before the worker stops; lower = cheaper, higher = fewer cold starts.
SCALEDOWN_WINDOW = int(os.environ.get("KREA2_SCALEDOWN", "300"))

# Video runs in its own container: Wan 2.2's 5B transformer would otherwise compete
# with the 12B DiT for the same 80 GB. Its idle window is short, because a clip costs
# minutes of GPU time and there is no reason to keep the card warm afterwards.
VIDEO_GPU = os.environ.get("KREA2_VIDEO_GPU", GPU)
VIDEO_SCALEDOWN = int(os.environ.get("KREA2_VIDEO_SCALEDOWN", "180"))

# Two I2V checkpoints. The 5B is one transformer (fast, 34 GB on disk); A14B is a
# 28B mixture-of-experts (126 GB on disk) whose two 57 GB experts swap across PCIe at
# the noise boundary, because both cannot be resident on an 80 GB card.
VIDEO_MODELS = {
    "wan22-a14b": {
        "repo": os.environ.get("KREA2_VIDEO_A14B_REPO", "Wan-AI/Wan2.2-I2V-A14B-Diffusers"),
        "dir": "wan22-a14b",
        "label": "Wan 2.2 I2V-A14B · 28B MoE · best realism",
        "moe": True,
    },
    "wan22-5b": {
        "repo": os.environ.get("KREA2_VIDEO_REPO", "Wan-AI/Wan2.2-TI2V-5B-Diffusers"),
        "dir": "wan22",
        "label": "Wan 2.2 TI2V-5B · fast",
        "moe": False,
    },
}
DEFAULT_VIDEO_MODEL = os.environ.get("KREA2_VIDEO_MODEL", "wan22-a14b")
# 4K refine: of an 8-step schedule, half gets re-run (4 effective steps per tile).
UPSCALE_STRENGTH = 0.5
UPSCALE_STEPS = 8
UPSCALE_TILE = 1536

W = "/weights"
HF = "/cache/hf"

weights_vol = modal.Volume.from_name("krea2-weights", create_if_missing=True)
hf_vol = modal.Volume.from_name("krea2-hf-cache", create_if_missing=True)

# Peer table and usage ledger. Both are workspace-scoped by design: a Modal Dict is not
# shared across accounts, so cross-account discovery goes through ORBITAL_PEERS seeds and
# the shared ORBITAL_CLUSTER_TOKEN, never through this object alone.
peer_table = modal.Dict.from_name("orbital-peers", create_if_missing=True)
usage_ledger = modal.Dict.from_name("orbital-ledger", create_if_missing=True)
market_listings = modal.Dict.from_name("orbital-listings", create_if_missing=True)
# Characters distilled from visitor prompts. Shared by every node on the deployment, so a
# prompt submitted once shows up in the global gallery everywhere.
girlfriends = modal.Dict.from_name("orbital-girlfriends", create_if_missing=True)
# Which gallery characters already have a stock frame. A Dict is authoritative and fresh in
# every container, unlike a volume listing, which each container caches until a reload.
gallery_thumbs = modal.Dict.from_name("orbital-gallery-thumbs", create_if_missing=True)
# The ready-to-serve JPEG for each character, base64. A Dict is read from memory, so the
# gallery does not pay a volume fetch per card — the volume stays the durable copy.
gallery_blobs = modal.Dict.from_name("orbital-gallery-blobs", create_if_missing=True)
# Per-visitor chat memory: one entry per (character, visitor) holding the transcript and the
# facts she has been told, plus a `profile:<visitor>` entry holding the VLM's reading of
# that visitor's profile picture. A Dict is shared by every container and survives a deploy.
chats = modal.Dict.from_name("orbital-chats", create_if_missing=True)
# Each character's upgraded personal gallery (escort pages): the 1K/2K/4K ladder, the VLM's
# reading of her, and the AIGF / AIGF Pro pitch her page shows.
upsell = modal.Dict.from_name("orbital-upsell", create_if_missing=True)
# The Velvet HUD's money and its orders. The L$ is taken in-world by `llGiveMoney` and then
# booked here, so the balance has to live beside the catalog, not in one container's memory:
# every web container and the GPU worker read the same Modal Dict.
wallets = modal.Dict.from_name("orbital-wallets", create_if_missing=True)
commissions = modal.Dict.from_name("orbital-commissions", create_if_missing=True)
# Orders the rail REFUSED because the wallet was short. A stale HUD copy takes its L$ in-world
# before it asks the server, so the refusal is the only record that a client paid and got
# nothing — and the only place their name and key sit together. Keyed by client key.
refusals = modal.Dict.from_name("orbital-refusals", create_if_missing=True)
MARKET_SECRET = os.environ.get("MARKET_SECRET", "").strip()

import orbit_chat  # noqa: E402
import orbit_guard  # noqa: E402
import orbit_studio  # noqa: E402
import orbit_culture  # noqa: E402
import orbit_econ  # noqa: E402  (local module, mounted with the app)
import orbit_girls  # noqa: E402
import orbit_market  # noqa: E402
import orbit_net  # noqa: E402
import orbit_upsell  # noqa: E402
import orbit_engine  # noqa: E402  (routes each paid frame to the cheapest host that pays)

NODE_ID = os.environ.get("ORBITAL_NODE_ID", "orbital")
SELF_URL = os.environ.get("ORBITAL_SELF_URL", "").strip().rstrip("/")
CLUSTER_TOKEN = os.environ.get("ORBITAL_CLUSTER_TOKEN", "").strip()
SEED_PEERS = [p.strip().rstrip("/") for p in os.environ.get("ORBITAL_PEERS", "").split(",") if p.strip()]

# Custom domains are bound at deploy time from the shell: ORBITAL_CUSTOM_DOMAIN=dubwarz.vip.
# Modal only accepts these on Team/Enterprise, so an empty list keeps the default URL working.
CUSTOM_DOMAINS = [d.strip() for d in os.environ.get("ORBITAL_CUSTOM_DOMAIN", "").split(",") if d.strip()]
SITE_DOMAIN = os.environ.get("ORBITAL_SITE_DOMAIN", "dubwarz.vip").strip().lower()
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "").strip()
STRIPE_CONNECTED_ACCOUNT = os.environ.get("STRIPE_CONNECTED_ACCOUNT", "").strip()
DEVELOPER_BPS = orbit_econ.clamp_bps(os.environ.get("STRIPE_DEVELOPER_BPS", orbit_econ.DEFAULT_DEVELOPER_BPS))

# The Velvet rail's Linden prices. Second Life trades around L$250 per USD, so the quote is
# one number rather than a moving rate: a test frame is L$100, a picture L$250, a video
# L$750. The HUD draws its buttons from `/api/roster` and the server prices the order from
# this same table, so the button and the debit cannot disagree.
LINDEN_RATE = int(os.environ.get("VELVET_LINDEN_RATE", "250") or "250")
COMMISSION_USD = {"test": 0.40, "image": 1.00, "video": 3.00}
# How each kind is rendered. 8 steps is the studio's interactive default and it does not
# survive a gallery card: the first test order came back soft because the whole frame is
# cropped to 4:5 and the face gets only a slice of those steps. A commissioned frame is
# billed, so it renders at gallery tier — 20 steps for the test proof, 24 for a paid
# picture, both at 1024x1280. The numbers ride on the order record, so a receipt states
# exactly what ran instead of a number remembered from the code.
# `generate` fixes the schedule at 8 steps / CFG 0 — the core is distilled for it and a
# request cannot change it — so quality here comes from the two levers that DO exist: native
# resolution (more pixels on the face before the 4:5 crop) and the tile refine pass.
COMMISSION_RENDER = {
    "test":  {"width": 1280, "height": 1600, "upscale": 0},
    "image": {"width": 1280, "height": 1600, "upscale": 2048},
}
# Test frames are the client's eyes-only proof. This tuple is the enforcement point: a test
# order is marked private at creation, and nothing private is ever written into
# gallery/thumbs or a character record — the only copy is the order's own page and frame.
PRIVATE_KINDS = ("test",)
# The catalog prompts describe “who and where”; they never ask for a photograph. A distilled
# 8-step render left to itself returns the waxy, airbrushed face the first test orders showed.
# These are the render tokens that pull it back to Krea 2's realism, appended to every
# commission prompt after the client's note so the subject survives and the finish is named.
COMMISSION_QUALITY = (
    "raw photo, photorealistic, ultra-detailed natural skin texture with visible pores and "
    "fine vellus hair, realistic subsurface scattering, natural makeup, no plastic or "
    "airbrushed skin, sharp focus on the eyes, 85mm lens, subtle film grain, "
    "clean background bokeh, no motion blur"
)
COMMISSION_DIR = "gallery/commissions"
# The key the HUD signs its wallet calls with. The L$ has already moved in-world by the time
# a request arrives, so this key is a sanity check on the rail, not the thing that makes
# the payment real.
CLAIM_KEY = os.environ.get("SLGF_CLAIM_SECRET", "GETJINXED_COMMON_TEST").strip()


def linden_price(kind: str) -> int:
    """L$ for one commission kind — rounded once, here, so every caller agrees."""
    return int(round(float(COMMISSION_USD.get(str(kind)) or 0.0) * LINDEN_RATE))


def _wallet_by_name(name: str):
    """Find the wallet belonging to a resident by display name, newest first.

    Second Life names are not unique history — a resident can rename — so this only ever
    answers a reconciliation question ("whose wallet is 'Yababa Earthboy'?"), never an
    authentication one.
    """
    want = " ".join(str(name or "").split()).lower()
    if not want:
        return None, None
    hits = []
    for key in wallets.keys():
        row = wallets.get(key)
        if isinstance(row, dict) and " ".join(str(row.get("resident") or "").split()).lower() == want:
            hits.append((int(row.get("updated") or 0), key, dict(row)))
    if not hits:
        return None, None
    hits.sort(key=lambda h: h[0])
    return hits[-1][1], hits[-1][2]


def _order_key(order_id: str) -> str:
    """Resolve an order id to the key actually stored in the Dict.

    HUD builds in the wild decorate ids for their own bookkeeping (`comm-<id>`, `order-<id>`)
    and clients paste whatever their copy printed. The store only ever holds the bare id, so a
    decorated link must not read as "unknown order" — that is a delivered frame the client is
    told does not exist.
    """
    want = str(order_id or "").strip()
    if not want:
        return ""
    if isinstance(commissions.get(want), dict):
        return want
    bare = want
    for prefix in ("comm-", "order-", "velvet-", "v-"):
        if bare.lower().startswith(prefix):
            bare = bare[len(prefix):]
            if isinstance(commissions.get(bare), dict):
                return bare
    # Last resort: the tail of a differently-decorated id still has to land on a real order.
    tail = want.lower()[-12:]
    for key in commissions.keys():
        if str(key).lower().endswith(tail) and isinstance(commissions.get(key), dict):
            return str(key)
    return want


def _wallet_row(client: str) -> dict:
    row = wallets.get(str(client or ""))
    return dict(row) if isinstance(row, dict) else {"balanceL": 0, "spentL": 0}


def _wallet_add(client: str, delta: int, note: str = "", resident: str = "") -> dict:
    """Move a wallet by `delta` L$ and return the new row. One writer, one shape."""
    key = str(client or "")
    row = _wallet_row(key)
    balance = int(row.get("balanceL") or 0) + int(delta)
    row["balanceL"] = balance
    if int(delta) < 0:
        row["spentL"] = int(row.get("spentL") or 0) - int(delta)
    if resident:
        row["resident"] = str(resident)[:64]
    if note:
        row["last"] = str(note)[:120]
    row["updated"] = int(time.time())
    wallets[key] = row
    return row

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "libgl1", "libglib2.0-0")
    .pip_install(
        # torchvision is not used by the render path, but Qwen3-VL's processor imports its
        # video preprocessor, so without it AutoProcessor (and with it image input to chat)
        # dies with "AutoVideoProcessor requires the Torchvision library".
        "torch==2.9.0",
        "torchvision==0.24.0",
        index_url="https://download.pytorch.org/whl/cu128",
    )
    .pip_install(
        "transformers==4.57.1",
        "diffusers>=0.35.0",
        "safetensors>=0.4.5",
        "einops>=0.7.0",
        "pillow>=10.0.0",
        "accelerate>=1.0.0",
        # diffusers' load_lora_weights needs the PEFT backend for Wan LoRAs
        "peft>=0.13.0",
        "hf_transfer>=0.1.8",
        "sentencepiece",
        # Wan's UMT5 tokenizer references ftfy without importing it in this stack
        "ftfy>=6.2.0",
        "fastapi[standard]>=0.115.0",
        "click>=8.4.1",
        # mp4 muxing for the video worker
        "imageio>=2.36.0",
        "imageio-ffmpeg>=0.5.1",
    )
    .env(
        {
            "HF_HOME": HF,
            "HF_HUB_ENABLE_HF_TRANSFER": "1",
            "PYTHONPATH": "/root/krea2",
            "TOKENIZERS_PARALLELISM": "false",
            # The 2K VAE decode asks for a ~20 GiB conv workspace; without expandable
            # segments PyTorch hoards 20+ GiB of reserved-but-unallocated blocks.
            "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
        }
    )
    .add_local_dir("krea2", remote_path="/root/krea2", copy=True)
    # app.py imports these at module level, so every container that imports the app needs
    # them on the path — not just the web image. Omit this and every GPU function dies at
    # startup with ModuleNotFoundError while /health still answers from the web tier.
    .add_local_python_source("orbit_net", "orbit_econ", "orbit_market", "orbit_girls",
                            "orbit_culture", "orbit_chat", "orbit_upsell", "orbit_guard",
                            "orbit_studio", "orbit_engine")
)

# --------------------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------------------

DIT_CONFIG = dict(
    features=6144,
    tdim=256,
    txtdim=2560,
    heads=48,
    kvheads=12,
    multiplier=4,
    layers=28,
    patch=2,
    channels=16,
    txtheads=20,
    txtkvheads=20,
    txtlayers=12,
)

# Both files carry the exact `SingleStreamDiT` key names (verified against the official
# release), so they are interchangeable at load time.
DITS = {
    "uncensored": {
        "repo": "Quiho/krea2-turbo-uncensored-v1.1-BACKUP",
        "file": "split/diffusion_models/Krea2_turbo_uncensored_edit_v1.1-fp8_scaled.safetensors",
        "local": "krea2_turbo_uncensored_v1.1_fp8.safetensors",
        "label": "Turbo · uncensored v1.1 (fp8 -> bf16)",
    },
    "krea": {
        "repo": "Comfy-Org/Krea-2",
        "file": "diffusion_models/krea2_turbo_bf16.safetensors",
        "local": "krea2_turbo_bf16.safetensors",
        "label": "Turbo · official Krea 2 (bf16)",
    },
}

ENCODERS = {
    "abliterated": "saracen9/qwen3vl-4b-krea2-abliterated",
    "stock": "Qwen/Qwen3-VL-4B-Instruct",
}

SELECT_LAYERS = (2, 5, 8, 11, 14, 17, 20, 23, 26, 29, 32, 35)

# Dequantized once in `download()` so GPU cold starts load plain bf16.
MATERIALIZED = {"uncensored": "krea2_turbo_uncensored_v1.1_bf16.safetensors"}


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _snap(v: int) -> int:
    """The latent grid is patchified 8x2, so sizes must be multiples of 16."""
    return max(512, int(round(v / 16.0) * 16))


def _snap32(v: float) -> int:
    """Wan's VAE is 16x spatial / 4x temporal, so 32-px alignment is always safe."""
    return max(256, int(round(v / 32.0) * 32))


# --------------------------------------------------------------------------------------
# Per-image LoRA (fitted on the image itself, in a separate container)
# --------------------------------------------------------------------------------------

# --------------------------------------------------------------------------------------
# Custom LoRAs (user-supplied .safetensors, applied on demand)
# --------------------------------------------------------------------------------------

LORA_DIR = "loras"
CUSTOM_LORAS = {
    "michelle": {
        "label": "Michelle · character (Krea 2, NSFW)",
        "file": "michelle_krea2.safetensors",
        "source": "https://civitai.com/models/2772050",
        "url": "https://civitai.com/api/download/models/3121136?fileId=3001463",
        "default_scale": 1.0,
    },
    "breastshape": {
        "label": "Breast Shape · concept (Krea 2, NSFW)",
        "file": "2mybreastsA_Krea2_FINAL_LoRA.safetensors",
        "source": "https://civitai.com/models/3003699",
        "url": "https://civitai.com/api/download/models/3451303?fileId=3351385",
        "trigger": "mybreastsA",
        "default_scale": 1.0,
    },
    "bimbo": {
        "label": "Bimbo · style (Krea 2, NSFW)",
        "file": "bimboLoraForKrea2_v1.safetensors",
        "source": "https://civitai.com/models/3008606",
        "url": "https://civitai.com/api/download/models/3413929?fileId=3306526",
        "trigger": "bmbo",
        "default_scale": 1.0,
    },
    "titjob": {
        "label": "Titjob / Paizuri · pose (Krea 2, NSFW)",
        "file": "Titjob_Krea2.safetensors",
        "source": "https://civitai.com/models/2974641",
        "url": "https://civitai.com/api/download/models/3371259?fileId=3259491",
        "trigger": "titjob",
        "default_scale": 1.0,
    },
    "liza": {
        "label": "Liza · character (Krea 2, NSFW)",
        "file": "Liza_Krea2_V1.safetensors",
        "source": "https://civitai.com/models/2987031",
        "url": "https://civitai.com/api/download/models/3386640?fileId=3275720",
        "trigger": "Liza",
        "default_scale": 1.0,
    },
    "annai": {
        "label": "Annai · character (Krea 2, NSFW)",
        "file": "Annai-Krea2.safetensors",
        "source": "https://civitai.com/models/2785776",
        "url": "https://civitai.com/api/download/models/3138694?fileId=3018765",
        "trigger": "Annai",
        "default_scale": 1.0,
    },
    "lily": {
        "label": "Lily · character (Krea 2, NSFW)",
        "file": "Lily_Krea2_V1.safetensors",
        "source": "https://civitai.com/models/3030293",
        "url": "https://civitai.com/api/download/models/3442344?fileId=3346099",
        "trigger": "Lily",
        "default_scale": 1.0,
    },
    "estelle": {
        "label": "Estelle · character (Krea 2, NSFW)",
        "file": "lora_Estelle_000002500_Krea-2-Raw_rc117_v3.safetensors",
        "source": "https://civitai.com/models/2823738",
        "url": "https://civitai.com/api/download/models/3185441?fileId=3065979",
        "trigger": "Estelle",
        "default_scale": 1.0,
    },
    "natalie": {
        "label": "Natalie · character (Krea 2, NSFW)",
        "file": "Natalie_Krea2_V1.safetensors",
        "source": "https://civitai.com/models/3013738",
        "url": "https://civitai.com/api/download/models/3420678?fileId=3317082",
        "trigger": "Natalie",
        "default_scale": 1.0,
    },
    "rachelle": {
        "label": "Rachelle · character (Krea 2)",
        "file": "Rachelle_V1_CharacterLora_R4PT0R.safetensors",
        "source": "https://civitai.com/models/2982129",
        "url": "https://civitai.com/api/download/models/3380602?fileId=3269123",
        "trigger": "Rachelle",
        "default_scale": 1.0,
    },
    "eyes": {
        "label": "Just Eyes · style (Krea 2)",
        "file": "just-eyes-v3.2400.safetensors",
        "source": "https://civitai.com/models/2978156",
        "url": "https://civitai.com/api/download/models/3375686?fileId=3263979",
        "trigger": "j2st-3y3s",
        "default_scale": 0.6,
    },
    "laundromat": {
        "label": "Laundromat · concept (Krea 2)",
        "file": "Midnight_Laundromat___KREA2.safetensors",
        "source": "https://civitai.com/models/2945185",
        "url": "https://civitai.com/api/download/models/3334863?fileId=3221260",
        "trigger": "laundryafterdark",
        "default_scale": 1.0,
    },
    "steph": {
        "label": "Steph LDS · character (Krea 2)",
        "file": "lora_Steph-lds_Krea-2-Raw_rc210_v1.safetensors",
        "source": "https://civitai.com/models/3017780",
        "url": "https://civitai.com/api/download/models/3426010?fileId=3320960",
        "trigger": "Steph-lds",
        "default_scale": 1.0,
    },
    # A Velvet performer's own adapter. No Civitai model can be her face, so there is no
    # `url`: the weights are fitted here and dropped into the volume's `loras/` folder as
    # this file. `_lora_catalog()` scans that folder, so the adapter goes live the moment
    # the file lands — no deploy, and no download for `--lora-all` to attempt.
    "sophia": {
        "label": "Sophia · character (Krea 2, fitted for her profile)",
        "file": "sophia_krea2.safetensors",
        "source": "fitted from her own profile render",
        "trigger": "sophia",
        # 0.6, not 1.0. The adapter was fitted from a Second Life screenshot, so at full
        # strength it repaints the whole face in that screenshot's terms: waxy skin, painted
        # eyebrows, overdrawn lips. At 0.6 her identity survives and the base model's realism
        # wins the skin, which is exactly what a gallery frame needs.
        "default_scale": 0.6,
        "local": True,
    },
    "kessa": {
        "label": "Kessa · character (Krea 2, fitted for her profile)",
        "file": "kessa_krea2.safetensors",
        "source": "fitted from her own profile render",
        "trigger": "kessa",
        "default_scale": 1.0,
        "local": True,
    },
}

# Video LoRAs are separate: they are trained against a Wan transformer, live in their own
# folder, and are loaded by diffusers rather than by the image-side key matcher.
VIDEO_LORAS = {
    "throat-a14b": {
        "label": "Deepthroat / face fuck · Wan 2.2 I2V-A14B (v3.0 High)",
        "file": "Wan22_ThroatV3_High.safetensors",
        "source": "https://civitai.com/models/2023407?modelVersionId=2517513",
        "url": "https://civitai.com/api/download/models/2517513?fileId=2405261",
        "trigger": (
            "a woman is kneeling in front of a man, she is giving him a deepthroat "
            "blowjob, almost the entire penis is in her mouth, he is grabbing her head "
            "and holding it in place"
        ),
        "base": "Wan Video 2.2 I2V-A14B",
        "models": ["wan22-a14b"],
        "default_scale": 1.0,
    },
    "throat-low": {
        "label": "Deepthroat / face fuck · Wan 2.2 I2V-A14B (v3.0 Low)",
        "file": "Wan22_ThroatV3_Low.safetensors",
        "source": "https://civitai.com/models/2023407?modelVersionId=2517548",
        "url": "https://civitai.com/api/download/models/2517548?fileId=2405303",
        "trigger": "",
        "base": "Wan Video 2.2 I2V-A14B",
        "models": ["wan22-a14b"],
        "default_scale": 1.0,
    },
    "throat-h3": {
        "label": "Deepthroat / face fuck · MiniMax H3 (v0.1) — wrong base for this app",
        "file": "MMH3_AllInOne-000004.comfy.safetensors",
        "source": "https://civitai.com/models/2023407?modelVersionId=3374369",
        "url": "https://civitai.com/api/download/models/3374369?fileId=3262606",
        "trigger": (
            "a woman is kneeling in front of a man, his penis is out, he is grabbing her "
            "head with his hand, she is looking up at him, he is face fucking her"
        ),
        "base": "MiniMax H3",
        # Nothing here runs MiniMax: kept so the file can be fetched and used elsewhere,
        # but the Wan pipelines refuse it instead of silently loading dead weights.
        "models": [],
        "default_scale": 1.0,
    },
    "bounce-a14b": {
        "label": "Bouncing boobs · Wan 2.2 I2V-A14B (High)",
        "file": "BounceHighWan2_2.safetensors",
        "source": "https://civitai.com/models/1343431?modelVersionId=2191217",
        "url": "https://civitai.com/api/download/models/2191217?fileId=2084187",
        "trigger": "her breasts are bouncing",
        "base": "Wan Video 2.2 I2V-A14B",
        "models": ["wan22-a14b"],
        "default_scale": 1.0,
    },
    "bounce-low": {
        "label": "Bouncing boobs · Wan 2.2 I2V-A14B (Low)",
        "file": "BounceLowWan2_2.safetensors",
        "source": "https://civitai.com/models/1343431?modelVersionId=2191270",
        "url": "https://civitai.com/api/download/models/2191270?fileId=2084219",
        "trigger": "her breasts are bouncing",
        "base": "Wan Video 2.2 I2V-A14B",
        "models": ["wan22-a14b"],
        "default_scale": 1.0,
    },
    "bounce-h3": {
        "label": "Bouncing boobs · MiniMax H3 (FL2VA v0.7) — wrong base for this app",
        "file": "bounceV07_fl2va-000230_Intense.safetensors",
        "source": "https://civitai.com/models/1343431?modelVersionId=3258590",
        "url": "https://civitai.com/api/download/models/3258590?fileId=3141848",
        "trigger": "her breast is bouncing up and down",
        "base": "MiniMax H3",
        "models": [],
        "default_scale": 1.0,
    },
    "cumshot-h3": {
        "label": "Epic Cumshots · MiniMax H3 (ALPHA) — no Wan build exists",
        "file": "epic_cumshots-MiniMaxH3-ALPHA-CUMSH0T.safetensors",
        "source": "https://civitai.com/models/2621242?modelVersionId=3202064",
        "url": "https://civitai.com/api/download/models/3202064?fileId=3083352",
        "trigger": "",
        "base": "MiniMax H3",
        # The model page offers MiniMax H3 and LTX 2.3 only, so there is no Wan variant to
        # pair with this app's pipelines. Registered so the file can be fetched anyway.
        "models": [],
        "default_scale": 1.0,
    },
    "blowjob-h3": {
        "label": "Messy Blowjob · MiniMax H3 (v1.0) — no Wan build exists",
        "file": "MessyBlowjob_v1_H3.safetensors",
        "source": "https://civitai.com/models/2997232?modelVersionId=3399472",
        "url": "https://civitai.com/api/download/models/3399472?fileId=3289886",
        "trigger": "",
        "base": "MiniMax H3",
        # Single-version model, H3 only: fetchable, never loadable by the Wan workers.
        "models": [],
        "default_scale": 1.0,
    },
}

_LORA_SUFFIX = re.compile(
    r"^(?P<path>.+?)\.(?P<kind>lora_A|lora_B|lora_down|lora_up|lora_alpha|alpha)(?:\.weight)?$"
)


_LAST_LORA_RELOAD = 0.0


def _reload_loras(max_age: float = 10.0) -> None:
    """Pick up LoRA files another container committed to the volume.

    A Modal volume is only re-scanned on `reload()`: the image worker stays warm for
    hours, while `/api/lora/download_all` runs in its own container, so without this
    every freshly downloaded file still reported `available: false` here.
    """
    global _LAST_LORA_RELOAD
    now = time.time()
    if now - _LAST_LORA_RELOAD < max_age:
        return
    _LAST_LORA_RELOAD = now
    try:
        weights_vol.reload()
    except Exception as exc:  # a read-only or detached volume: keep the current view
        print(f"[lora-reload] skipped: {type(exc).__name__}: {exc}")


# The house adapters every render wears. They live in `studio.json` on the weights volume, so
# an admin changes the pair without a deploy (`POST /api/studio/config`); the values below are
# only what applies before the first write. Nothing here is reachable from the studio page.
STUDIO_CONFIG_NAME = "studio.json"
STUDIO_HOUSE_DEFAULT = {"loras": [{"name": "bimbo", "scale": 1.0},
                                  {"name": "breastshape", "scale": 1.0}]}
_studio_cfg_cache: dict = {"at": 0.0, "value": None}


def _studio_config() -> dict:
    """The admin's house-adapter list: the volume file, else the built-in default.

    Cached 30 s per container — the file only changes when an admin writes it, and a
    `weights_vol.reload()` is what makes a fresh write visible to a warm worker.
    """
    import json

    now = time.time()
    if _studio_cfg_cache["value"] is not None and now - float(_studio_cfg_cache["at"]) < 30:
        return _studio_cfg_cache["value"]
    value = json.loads(json.dumps(STUDIO_HOUSE_DEFAULT))
    try:
        with weights_vol.open(STUDIO_CONFIG_NAME, "r") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict) and isinstance(loaded.get("loras"), list):
            value = {"loras": loaded["loras"]}
    except Exception:
        pass
    _studio_cfg_cache.update({"at": now, "value": value})
    return value


def _lora_catalog() -> dict:
    """Registry plus anything else sitting in /weights/loras."""
    _reload_loras()
    cat = {
        k: {
            "label": v["label"],
            "file": v["file"],
            "source": v.get("source"),
            "trigger": v.get("trigger"),
            "default_scale": v.get("default_scale", 1.0),
        }
        for k, v in CUSTOM_LORAS.items()
    }
    d = os.path.join(W, LORA_DIR)
    known = {v["file"] for v in cat.values()}
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith(".safetensors") and f not in known:
                cat.setdefault(
                    f[: -len(".safetensors")],
                    {"label": f[: -len(".safetensors")], "file": f, "source": None,
                     "default_scale": 1.0},
                )
    for v in cat.values():
        v["available"] = os.path.exists(os.path.join(d, v["file"]))
    return cat


def _video_lora_catalog() -> dict:
    """Video LoRAs: the registry plus anything in /weights/loras/video."""
    _reload_loras()
    cat = {
        k: {
            "label": v["label"],
            "file": v["file"],
            "source": v.get("source"),
            "trigger": v.get("trigger") or "",
            "base": v.get("base"),
            "models": None if v.get("models") is None else list(v["models"]),
            "default_scale": v.get("default_scale", 1.0),
        }
        for k, v in VIDEO_LORAS.items()
    }
    d = os.path.join(W, LORA_DIR, "video")
    known = {v["file"] for v in cat.values()}
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith(".safetensors") and f not in known:
                cat.setdefault(
                    f[: -len(".safetensors")],
                    # models=None means "base unknown" (a hand-dropped file): allowed.
                    {"label": f[: -len(".safetensors")], "file": f, "source": None,
                     "trigger": "", "base": None, "models": None, "default_scale": 1.0},
                )
    for v in cat.values():
        v["available"] = os.path.exists(os.path.join(d, v["file"]))
    return cat


def _safetensors_ok(path: str) -> bool:
    """Header check: 8-byte little-endian length, then a JSON metadata block.

    Civitai answers an unauthenticated NSFW download with an HTML login page and HTTP
    200, so the status code proves nothing — the bytes have to be inspected.
    """
    import json
    import struct

    try:
        with open(path, "rb") as f:
            head = f.read(8)
            if len(head) < 8:
                return False
            n = struct.unpack("<Q", head)[0]
            if not 0 < n < 50_000_000:
                return False
            return isinstance(json.loads(f.read(n)), dict)
    except Exception:
        return False


# No key is shipped in this file. Resolution order: CIVITAI_TOKEN (secret or env), then
# loras/.civitai_token in the volume (0600), then this opt-in fallback, which is empty
# unless an operator deliberately sets CIVITAI_TOKEN_DEFAULT at deploy time. Storing an
# empty string in the volume file disables downloads until a key is set again.
DEFAULT_CIVITAI_TOKEN = os.environ.get("CIVITAI_TOKEN_DEFAULT", "").strip()

# Optional operator gate for the mutating LoRA routes. Unset means they stay open, which
# is the previous behaviour; set it and every write needs X-Access-Key (or a bearer token).
ACCESS_KEY = os.environ.get("ORBITAL_ACCESS_KEY", "").strip()

# `from __future__ import annotations` stringifies the route signatures, and FastAPI resolves
# those against this module's globals — so Request/HTTPException have to live here, not only
# inside web(). The import is optional so a local `modal deploy` works without fastapi.
try:
    from fastapi import HTTPException, Request  # noqa: F401  (referenced by annotations)
except Exception:  # pragma: no cover - local deploy without fastapi installed
    HTTPException = Request = None  # type: ignore[assignment]

# Deploy-time shell env does not reach a container on its own. Forward the two keys an
# operator may export into a Modal Secret, and only when they are actually set — an empty
# secret would be worse than none. Workers read the token from the volume, so the secret is
# only attached to the web function.
_ENV_KEYS = (
    "ORBITAL_ACCESS_KEY",
    "CIVITAI_TOKEN",
    "ORBITAL_CLUSTER_TOKEN",
    "ORBITAL_PEERS",
    "ORBITAL_NODE_ID",
    "ORBITAL_SELF_URL",
    "STRIPE_SECRET_KEY",
    "STRIPE_CONNECTED_ACCOUNT",
    "STRIPE_DEVELOPER_BPS",
)
_ENV_SECRETS = (
    [modal.Secret.from_dict({k: os.environ[k] for k in _ENV_KEYS if os.environ.get(k)})]
    if any(os.environ.get(k) for k in _ENV_KEYS)
    else []
)


def _lora_token() -> str:
    """Civitai token: environment, then the volume file, then the opt-in default."""
    tok = os.environ.get("CIVITAI_TOKEN", "").strip()
    if tok:
        return tok
    path = os.path.join(W, LORA_DIR, ".civitai_token")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            pass
    return DEFAULT_CIVITAI_TOKEN


def _fetch_lora(name: str, url: str = "", token: str = "", force: bool = False) -> dict:
    """Download one LoRA into the volume from inside the server.

    Shared by the worker (on first use of a custom LoRA), the `/api/lora/download`
    route and `modal run app.py --lora-download=<name>`.
    """
    import shutil
    import time as _time
    import urllib.request
    from pathlib import Path

    image_cat = _lora_catalog()
    video_cat = _video_lora_catalog()
    subdir = ""
    if name in image_cat:
        spec = image_cat[name]
        if spec.get("local") and not url:
            # Her own fitted weights: there is nothing to fetch, so this is a state to
            # report, not an error to raise.
            return {"name": name, "file": spec["file"], "local_only": True,
                    "available": os.path.exists(Path(W) / LORA_DIR / spec["file"])}
    elif name in video_cat:
        spec = video_cat[name]
        subdir = "video"
    else:
        if not url:
            known = list(image_cat) + list(video_cat)
            raise RuntimeError(f"unknown LoRA '{name}' — known: {', '.join(known) or 'none'}")
        spec = {"file": f"{name}.safetensors"}
    dest = Path(W) / LORA_DIR / subdir / spec["file"]
    # `force` is what a re-fit needs: her file already exists, and training a better one
    # must replace it rather than report the old weights as cached.
    if not force and dest.exists() and _safetensors_ok(str(dest)):
        return {"name": name, "file": spec["file"], "cached": True,
                "mb": round(dest.stat().st_size / 1e6, 1)}

    target = (
        url
        or (CUSTOM_LORAS.get(name) or {}).get("url")
        or (VIDEO_LORAS.get(name) or {}).get("url")
        or ""
    )
    if not target:
        raise RuntimeError(
            f"'{name}' has no download URL — drop {spec['file']} into the volume's "
            f"{LORA_DIR}/ folder to use it"
        )
    tok = token or _lora_token()
    if "civitai" in target and not tok:
        # Public files come down fine anonymously; the safetensors check below turns the
        # login page of a gated one into a clear "add a token" error.
        print(f"[lora-dl] {name}: no Civitai token set yet — trying anonymously")

    full = f"{target}{'&' if '?' in target else '?'}token={tok}" if "civitai" in target else target
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    t0 = _time.time()
    print(f"[lora-dl] {name} <- {target} (token {'set' if tok else 'none'})")
    req = urllib.request.Request(full, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        ctype = r.headers.get("Content-Type", "")
        print(f"[lora-dl] {r.status} {ctype}")
        with open(part, "wb") as f:
            shutil.copyfileobj(r, f, length=1 << 20)

    if not _safetensors_ok(str(part)):
        mb = part.stat().st_size / 1e6 if part.exists() else 0.0
        part.unlink(missing_ok=True)
        raise RuntimeError(
            f"'{name}' did not download as weights ({ctype or 'no content-type'}, "
            f"{mb:.1f} MB) — Civitai sent its login page, so the token is missing or was "
            "rejected. Paste one in the Custom LoRA panel (or run `modal run app.py "
            "--lora-token=<token>`); token: civitai.com → account settings → API keys."
        )
    os.replace(part, dest)
    weights_vol.commit()
    size = dest.stat().st_size
    print(f"[lora-dl] {name} ready: {dest.name} {size / 1e6:.1f} MB in {_time.time() - t0:.1f}s")
    return {"name": name, "file": spec["file"], "mb": round(size / 1e6, 1),
            "seconds": round(_time.time() - t0, 1), "cached": False}


def _key_prefixes(state: dict, limit: int = 3) -> str:
    """Where a file's keys actually point, so an incompatible LoRA explains itself.

    `diffusion_model.blocks.7.attn.wq.lora_A.weight` groups as `diffusion_model.blocks.N`;
    a Qwen-Image-style file groups as `transformer.text_fusion.layerwise_blocks`, which is
    instantly recognisable as the wrong architecture.
    """
    counts: dict[str, int] = {}
    for key in state:
        parts = [("N" if p.isdigit() else p) for p in key.split(".")[:-2] if p and p != "weight"]
        label = ".".join(parts[:4]) or key
        counts[label] = counts.get(label, 0) + 1
    if not counts:
        return "no tensors"
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
    return ", ".join(f"{name} ({n} keys)" for name, n in top)


def _normalize_lora_keys(state: dict) -> dict:
    """Fold the naming conventions (diffusers, PEFT, ComfyUI) onto DiT module paths."""
    pairs: dict[str, dict] = {}
    alphas: dict[str, float] = {}
    for key, value in state.items():
        name = key
        for prefix in (
            "model.diffusion_model.",
            "diffusion_model.",
            "transformer.",
            "unet.",
            "model.",
        ):
            if name.startswith(prefix):
                name = name[len(prefix) :]
                break
        if name.startswith("lora_unet_"):
            name = name[len("lora_unet_") :]
            match = _LORA_SUFFIX.match(name)
            if not match:
                continue
            path = match.group("path").replace("_", ".")  # blocks_0_attn_wq
            kind = match.group("kind")
        else:
            match = _LORA_SUFFIX.match(name)
            if not match:
                continue
            path = match.group("path")
            kind = match.group("kind")
        if kind in ("lora_alpha", "alpha"):
            try:
                alphas[path] = float(value.flatten()[0])
            except Exception:
                pass
            continue
        pairs.setdefault(path, {})["A" if kind in ("lora_A", "lora_down") else "B"] = value
    return {
        p: (d["A"], d["B"], alphas.get(p))
        for p, d in pairs.items()
        if "A" in d and "B" in d
    }


def _resolve_module(root, path: str):
    """Walk `blocks.0.attn.wq` on the DiT, returning (parent, attribute)."""
    node = root
    parts = path.split(".")
    for part in parts[:-1]:
        if part.isdigit():
            try:
                node = node[int(part)]
            except Exception:
                return None, None
        else:
            node = getattr(node, part, None)
        if node is None:
            return None, None
    return node, parts[-1]


def _make_custom_linear(base, pairs):
    """base(x) + Σ scale·(x·Aᵀ)·Bᵀ for every LoRA stacked on this layer.

    Several adapters live in one wrapper instead of nested wrappers, so the sum is one
    pass and unwrapping restores the original Linear exactly.
    """
    import torch
    from torch import nn

    class StackedLoRALinear(nn.Module):
        def __init__(self, layer, adapters):
            super().__init__()
            self.base = layer
            self.lora_A = nn.ParameterList(
                [nn.Parameter(a.detach().clone().float(), requires_grad=False) for a, _, _ in adapters]
            )
            self.lora_B = nn.ParameterList(
                [nn.Parameter(b.detach().clone().float(), requires_grad=False) for _, b, _ in adapters]
            )
            self.scales = [float(s) for _, _, s in adapters]

        def forward(self, x):
            out = self.base(x)
            for a, b, s in zip(self.lora_A, self.lora_B, self.scales):
                out = out + (((x.float() @ a.t()) @ b.t()) * s).to(x.dtype)
            return out

    return StackedLoRALinear(base, pairs)




LORA_RANK = int(os.environ.get("KREA2_LORA_RANK", "8"))
LORA_STEPS = int(os.environ.get("KREA2_LORA_STEPS", "40"))
LORA_LR = float(os.environ.get("KREA2_LORA_LR", "1e-3"))
LORA_MAXSIDE = int(os.environ.get("KREA2_LORA_MAXSIDE", "512"))


def _make_lora_linear(base, rank: int):
    """B starts at zero, so an untrained adapter is exactly a no-op on the base layer."""
    import torch
    from torch import nn

    class LoRALinear(nn.Module):
        def __init__(self, layer, r):
            super().__init__()
            self.base = layer
            self.r = r
            # Named lora_A / lora_B: PEFT's convention, and the name the training loop
            # and the state-dict writer both key off.
            # Follow the base layer's device: adapters are injected after the DiT has
            # been moved to the card, and a CPU mat2 fails inside the first forward.
            dev = layer.weight.device
            self.lora_A = nn.Parameter(torch.empty(r, layer.in_features, device=dev))
            self.lora_B = nn.Parameter(torch.zeros(layer.out_features, r, device=dev))
            torch.nn.init.kaiming_uniform_(self.lora_A, a=5**0.5)

        def forward(self, x):
            delta = (x.float() @ self.lora_A.t()) @ self.lora_B.t()
            return self.base(x) + delta.to(x.dtype)

    return LoRALinear(base, rank)


# The module set a Krea 2 LoRA conventionally adapts, copied from what ai-toolkit writes
# for these models: q/k/v/o, the attention gate, and gate/up/down inside the MLP.
LORA_TARGETS = (
    ("attn", "wq"), ("attn", "wk"), ("attn", "wv"), ("attn", "wo"), ("attn", "gate"),
    ("mlp", "gate"), ("mlp", "up"), ("mlp", "down"),
)


def _inject_lora(dit, rank: int) -> int:
    """Adapter on every projection a Krea 2 LoRA conventionally touches."""
    count = 0
    for block in dit.blocks:
        for parent_name, attr in LORA_TARGETS:
            parent = getattr(block, parent_name)
            setattr(parent, attr, _make_lora_linear(getattr(parent, attr), rank))
            count += 1
    return count


def _lora_tensors(dit) -> dict:
    """PEFT key names under the `diffusion_model.` prefix every Krea loader expects.

    A working Civitai Krea 2 LoRA (ai-toolkit output) keys its tensors as
    `diffusion_model.blocks.N.attn.wq.lora_A.weight`; a bare `blocks.N...` file is ignored
    by ComfyUI/SeaArt-style loaders even though the tensors are identical. Our own loader
    strips the prefix, so one file works in both places.
    """
    state = {}
    for i, block in enumerate(dit.blocks):
        for parent_name, attr in LORA_TARGETS:
            layer = getattr(getattr(block, parent_name), attr)
            for kind, tensor in (("lora_A", layer.lora_A), ("lora_B", layer.lora_B)):
                state[f"diffusion_model.blocks.{i}.{parent_name}.{attr}.{kind}.weight"] = (
                    tensor.detach().float().cpu().contiguous()
                )
    return state


def _dequant(state: dict, torch) -> dict:
    """ComfyUI single-file fp8_scaled (weight + per-tensor scale) -> plain bf16."""
    for sk in [k for k in state if k.endswith("_scale")]:
        wk = sk[: -len("_scale")]
        w = state[wk]
        state[wk] = w.to(torch.bfloat16).mul_(state.pop(sk).to(torch.bfloat16))
        del w
    return state


# --------------------------------------------------------------------------------------
# Weight preparation
# --------------------------------------------------------------------------------------


@app.function(
    image=image,
    volumes={W: weights_vol, HF: hf_vol},
    timeout=7200,
    cpu=16,
    memory=98304,
)
def download(variant: str = "all", force: bool = False) -> dict:
    """Pull DiTs + encoders + VAE into the volumes and validate the tensor layout."""
    import shutil
    import sys
    from pathlib import Path

    import torch
    from huggingface_hub import snapshot_download
    from safetensors import safe_open

    sys.path.insert(0, "/root/krea2")
    from mmdit import SingleMMDiTConfig, SingleStreamDiT

    root = Path(W)
    root.mkdir(parents=True, exist_ok=True)
    # Stage on the volume itself: the container's ephemeral disk is too small for 24 GB.
    staging = root / "_staging"
    staging.mkdir(parents=True, exist_ok=True)

    with torch.device("meta"):
        ref = SingleStreamDiT(SingleMMDiTConfig(**DIT_CONFIG))
    expected = set(ref.state_dict())
    del ref
    print(f"[download] reference state dict: {len(expected)} tensors")

    wanted = [variant] if variant in DITS else list(DITS)
    status = {}

    for name in wanted:
        spec = DITS[name]
        dest = root / spec["local"]
        if dest.exists() and dest.stat().st_size > 1e8 and not force:
            print(f"[download] cached {dest.name} ({dest.stat().st_size / 1e9:.1f} GB)")
        else:
            print(f"[download] fetching {spec['repo']}::{spec['file']}")
            snapshot_download(
                repo_id=spec["repo"],
                allow_patterns=[spec["file"]],
                local_dir=str(staging),
            )
            src = staging / spec["file"]
            os.replace(src, dest)
            print(f"[download] wrote {dest.name} ({dest.stat().st_size / 1e9:.1f} GB)")

        # Validate: same names, same shapes, ignoring fp8 scale companions.
        with safe_open(str(dest), framework="pt") as f:
            got = {k: tuple(f.get_slice(k).get_shape()) for k in f.keys() if not k.endswith("_scale")}
        missing = expected - set(got)
        extra = set(got) - expected
        if missing or extra:
            raise RuntimeError(
                f"{dest.name} key mismatch: {len(missing)} missing, {len(extra)} extra "
                f"e.g. {list(missing)[:3]} / {list(extra)[:3]}"
            )
        status[name] = {"path": str(dest), "gb": round(dest.stat().st_size / 1e9, 2), "tensors": len(got)}
        print(f"[download] {name}: layout OK ({len(got)} tensors)")

    if "uncensored" in wanted:
        from safetensors.torch import load_file, save_file

        src, dst = root / DITS["uncensored"]["local"], root / MATERIALIZED["uncensored"]
        if force or not dst.exists() or dst.stat().st_size < 1e8:
            print(f"[download] materializing bf16 {dst.name} (one time)")
            state = _dequant(load_file(str(src)), torch)
            for k in list(state):
                v = state[k]
                if v.ndim <= 1 and v.dtype != torch.float32:
                    state[k] = v.float()
            save_file(state, str(dst), metadata={"format": "pt"})
            del state
        print(f"[download] {dst.name} ({dst.stat().st_size / 1e9:.1f} GB)")
        status["uncensored_bf16"] = str(dst)

    # Text encoders (abliterated keeps only the diffusers-format weights).
    for kind, repo in ENCODERS.items():
        print(f"[download] encoder {kind} <- {repo}")
        snapshot_download(repo_id=repo, ignore_patterns=["fp8/*", "comfyui/*", "*.gguf"])
        status[f"encoder_{kind}"] = repo

    print("[download] VAE <- Qwen/Qwen-Image (vae/)")
    snapshot_download(repo_id="Qwen/Qwen-Image", allow_patterns=["vae/*", "model_index.json"])

    weights_vol.commit()
    hf_vol.commit()
    print("[download] volumes committed")
    return status


def _weights_complete(dest) -> bool:
    """A directory only counts as downloaded when every shard its index names exists.

    An interrupted `snapshot_download` leaves `transformer/` present but 1 shard of 12,
    which a directory-existence check happily accepts — and then the worker dies at load.
    """
    import json
    from pathlib import Path

    dest = Path(dest)
    if not (dest / "model_index.json").exists():
        return False
    for comp in ("transformer", "transformer_2", "text_encoder", "vae"):
        d = dest / comp
        if not d.exists():
            if comp in ("transformer", "vae"):
                return False
            continue
        indexes = list(d.glob("*.safetensors.index.json"))
        if indexes:
            weight_map = json.loads(indexes[0].read_text()).get("weight_map", {})
            if any(not (d / f).exists() for f in set(weight_map.values())):
                return False
        elif not list(d.glob("*.safetensors")) and not list(d.glob("*.bin")):
            return False
    return True


@app.function(image=image, volumes={W: weights_vol}, timeout=300, cpu=1, memory=1024)
def store_lora_token(token: str = "") -> dict:
    """Keep the Civitai token in the volume so the server can fetch LoRAs by itself."""
    from pathlib import Path

    path = Path(W) / LORA_DIR / ".civitai_token"
    path.parent.mkdir(parents=True, exist_ok=True)
    tok = token.strip()
    if not tok:
        path.unlink(missing_ok=True)
        weights_vol.commit()
        print("[lora] token cleared")
        return {"stored": False, "chars": 0}
    path.write_text(tok, encoding="utf-8")
    os.chmod(path, 0o600)
    weights_vol.commit()
    print(f"[lora] token stored ({len(tok)} chars)")
    return {"stored": True, "chars": len(tok)}


@app.function(image=image, volumes={W: weights_vol, HF: hf_vol}, timeout=1800, cpu=4, memory=8192)
def download_all_loras() -> dict:
    """Fetch every registry LoRA the volume does not have yet."""
    out = {}
    for name in list(CUSTOM_LORAS) + list(VIDEO_LORAS):
        try:
            out[name] = _fetch_lora(name)
        except Exception as exc:
            out[name] = {"error": f"{type(exc).__name__}: {exc}"}
        print(f"[lora-all] {name}: {out[name]}")
    return out


@app.function(
    image=image,
    volumes={W: weights_vol, HF: hf_vol},
    timeout=1800,
    cpu=8,
    memory=16384,
)
def download_custom_lora(name: str = "michelle", url: str = "", token: str = "",
                         force: bool = False) -> dict:
    """Fetch a user LoRA into /weights/loras and check it is a real safetensors file.

    Civitai serves NSFW models only to authenticated requests: without a token the
    download endpoint answers 200 with an HTML page, so the bytes are sniffed instead
    of trusting the status code.
    """
    return _fetch_lora(name, url, token, force)


@app.function(
    image=image,
    volumes={W: weights_vol, HF: hf_vol},
    timeout=3600,
    cpu=16,
    memory=65536,
)
def download_video(model: str = "all", force: bool = False) -> dict:
    """Pull the video pipelines into the volume (34 GB for the 5B, 126 GB for A14B)."""
    from pathlib import Path

    from huggingface_hub import snapshot_download

    wanted = [model] if model in VIDEO_MODELS else list(VIDEO_MODELS)
    status = {}
    for name in wanted:
        spec = VIDEO_MODELS[name]
        dest = Path(W) / spec["dir"]
        dest.mkdir(parents=True, exist_ok=True)
        complete = _weights_complete(dest)
        if force or not complete:
            print(f"[video] fetching {spec['repo']} -> {dest}")
            snapshot_download(repo_id=spec["repo"], local_dir=str(dest))
        else:
            print(f"[video] cached {dest}")
        weights_vol.commit()
        size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 1e9
        status[name] = {"path": str(dest), "gb": round(size, 1), "repo": spec["repo"]}
        print(f"[video] {name} ready ({size:.1f} GB)")
    return status


# --------------------------------------------------------------------------------------
# GPU worker
# --------------------------------------------------------------------------------------


@app.cls(
    image=image,
    gpu=GPU,
    volumes={W: weights_vol, HF: hf_vol},
    timeout=1800,
    scaledown_window=SCALEDOWN_WINDOW,
    memory=65536,
    max_containers=1,
)
@modal.concurrent(max_inputs=1)
class Krea2:
    @modal.enter()
    def boot(self):
        import sys

        import torch

        sys.path.insert(0, "/root/krea2")
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        torch.backends.cudnn.benchmark = True
        torch.set_float32_matmul_precision("high")
        try:
            import torch._dynamo as dynamo

            dynamo.config.cache_size_limit = 128
            dynamo.config.suppress_errors = True  # fall back to eager, never to wrong math
        except Exception:
            pass

        # Load the text encoder in bf16 straight away instead of fp32-then-cast.
        import encoder as encmod

        # The Krea2 abliterated checkpoint ships a tokenizer_config where
        # `extra_special_tokens` is a list, which transformers 4.57 cannot parse.
        # Its vocabulary is byte-identical to the stock checkpoint, so borrow that.
        _orig_tok = encmod.AutoTokenizer.from_pretrained
        _orig_fast = encmod.Qwen2TokenizerFast.from_pretrained

        def _redirect(ident):
            return ENCODERS["stock"] if ident == ENCODERS["abliterated"] else ident

        encmod.AutoTokenizer.from_pretrained = staticmethod(
            lambda ident, *a, **kw: _orig_tok(_redirect(ident), *a, **kw)
        )
        encmod.Qwen2TokenizerFast.from_pretrained = staticmethod(
            lambda ident, *a, **kw: _orig_fast(_redirect(ident), *a, **kw)
        )

        _orig = encmod.Qwen3VLForConditionalGeneration.from_pretrained

        def _from_pretrained(*args, **kwargs):
            kwargs.setdefault("dtype", torch.bfloat16)
            kwargs.setdefault("low_cpu_mem_usage", True)
            # Some community finetunes ship a config.json without `rope_scaling`,
            # which Qwen3-VL's rotary embedding dereferences unconditionally.
            # Merge whatever the stock checkpoint declares into the loaded config.
            try:
                from transformers import AutoConfig

                ident = args[0] if args else kwargs.get("pretrained_model_name_or_path")
                cfg = AutoConfig.from_pretrained(ident)
                text_cfg = getattr(cfg, "text_config", None)
                if text_cfg is not None:
                    donor = AutoConfig.from_pretrained("Qwen/Qwen3-VL-4B-Instruct")
                    donor_rope = dict(getattr(donor.text_config, "rope_scaling", None) or {})
                    own_rope = getattr(text_cfg, "rope_scaling", None) or {}
                    merged = {**donor_rope, **own_rope}
                    if merged and merged != own_rope:
                        text_cfg.rope_scaling = merged
                        print(f"[encoder] rope_scaling patched: {merged}")
                kwargs["config"] = cfg
            except Exception as exc:
                print(f"[encoder] config fixup skipped: {exc}")
            return _orig(*args, **kwargs)

        encmod.Qwen3VLForConditionalGeneration.from_pretrained = _from_pretrained

        # cuDNN fused attention is what the official code asks for; fall back cleanly.
        import mmdit
        from einops import rearrange

        _attn = mmdit.attention

        def attention(q, k, v, mask=None, scale=None, gqa=False):
            try:
                return _attn(q, k, v, mask, scale, gqa)
            except Exception as exc:  # backend unsupported for this shape/mask
                print("[attention] cudnn backend failed, using torch SDPA:", exc)
                from torch.nn.attention import SDPBackend, sdpa_kernel

                with sdpa_kernel([SDPBackend.EFFICIENT_ATTENTION, SDPBackend.FLASH_ATTENTION]):
                    out = torch.nn.functional.scaled_dot_product_attention(
                        q, k, v, attn_mask=mask, scale=scale, enable_gqa=gqa
                    )
                return rearrange(out, "B H L D -> B L (H D)")

        mmdit.attention = attention

        self.dit = None
        self.ae = None
        self.encoder = None
        self.key = None
        self._defer_dit_reload = False
        self._custom = None
        self._custom_refs: list = []
        print(f"[boot] allocator: {os.environ.get('PYTORCH_CUDA_ALLOC_CONF')}")
        props = torch.cuda.get_device_properties(0)
        print(f"[boot] {torch.cuda.get_device_name(0)} · {props.total_memory / 1e9:.0f} GB · torch {torch.__version__}")

    # -- internals ---------------------------------------------------------------

    def _ensure(self, variant: str, encoder_kind: str):
        import gc

        import torch

        key = (variant, encoder_kind)
        if self.key == key:
            return
        if self.dit is not None:
            print(f"[load] unloading {self.key}")
            self.dit = self.ae = self.encoder = None
            gc.collect()
            torch.cuda.empty_cache()

        import sys

        sys.path.insert(0, "/root/krea2")
        from autoencoder import QwenAutoencoder
        from encoder import Qwen3VLConditioner
        from mmdit import SingleMMDiTConfig, SingleStreamDiT
        from safetensors.torch import load_file

        t0 = time.time()
        # A container is pinned to the volume snapshot taken when it started. The
        # one-time `download` job commits afterwards, so pull the live view first.
        for vol, label in ((weights_vol, "weights"), (hf_vol, "hf-cache")):
            try:
                vol.reload()
            except Exception as exc:
                print(f"[load] {label} reload skipped: {exc}")

        fp8_path = os.path.join(W, DITS[variant]["local"])
        bf16_path = os.path.join(W, MATERIALIZED[variant]) if variant in MATERIALIZED else ""
        path = bf16_path if bf16_path and os.path.exists(bf16_path) else fp8_path
        if not os.path.exists(path):
            raise RuntimeError(
                f"{path} missing from the weights volume — run "
                f"`modal run app.py::main --download-first` to populate it"
            )
        print(f"[load] {path} ({os.path.getsize(path) / 1e9:.1f} GB)")
        state = load_file(path, device="cpu")
        if any(k.endswith("_scale") for k in state):
            print("[load] dequantizing fp8 -> bf16 (materialized checkpoint not found)")
            state = _dequant(state, torch)
        for k in list(state):
            v = state[k]
            if v.ndim <= 1 and v.dtype != torch.float32:
                state[k] = v.float()

        with torch.device("meta"):
            dit = SingleStreamDiT(SingleMMDiTConfig(**DIT_CONFIG))
        dit.load_state_dict(state, strict=True, assign=True)
        del state
        gc.collect()
        self.dit = dit.to(device="cuda", dtype=torch.bfloat16).eval().requires_grad_(False)
        self.ae = (
            QwenAutoencoder().to(device="cuda", dtype=torch.bfloat16).eval().requires_grad_(False)
        )
        self.encoder = (
            Qwen3VLConditioner(ENCODERS[encoder_kind], 512, select_layers=SELECT_LAYERS)
            .to(device="cuda", dtype=torch.bfloat16)
            .eval()
            .requires_grad_(False)
        )
        self._install_decode_headroom()
        self._custom = None  # adapters live in the old weights
        self.key = key
        print(f"[load] ready in {time.time() - t0:.1f}s (dit={variant}, encoder={encoder_kind})")

    def _dit_to(self, device: str):
        """Park the 24 GB transformer; used to make room for high-res VAE decode."""
        import torch

        if self.dit is None:
            return
        target = torch.device(device)
        if next(self.dit.parameters()).device.type != target.type:
            self.dit.to(target)
            if target.type == "cpu":
                torch.cuda.empty_cache()

    def _encoder_to(self, device: str):
        """Park the 8.5 GB text encoder on CPU when it is not encoding."""
        import torch

        if self.encoder is None:
            return
        target = torch.device(device)
        if next(self.encoder.parameters()).device.type != target.type:
            self.encoder.to(target)
            if target.type == "cpu":
                torch.cuda.empty_cache()

    def _install_decode_headroom(self):
        """Make the whole-frame VAE decode fit at 2K.

        The Qwen VAE decodes the entire image at once and its causal conv3d stack
        asks cuDNN for a ~20 GiB workspace at 2048^2. Parking the text encoder
        (8.5 GB) always, and the DiT (24 GB) for high-resolution frames, is what
        lets that fit next to a resident 12B transformer on an 80 GB card.
        """
        import gc

        import torch

        orig = self.ae.decode

        def on_gpu(module):
            return module is not None and next(module.parameters()).device.type == "cuda"

        def decode(x):
            big = max(x.shape[-2], x.shape[-1]) >= 224  # >= ~1792 px output
            if on_gpu(self.encoder):
                self.encoder.to("cpu")
            parked_dit = False
            if big and on_gpu(self.dit):
                self.dit.to("cpu")
                parked_dit = True
            gc.collect()
            torch.cuda.empty_cache()
            if big:
                print(
                    f"[vae] decode {x.shape[-1] * 8}x{x.shape[-2] * 8}: "
                    f"alloc={torch.cuda.memory_allocated() / 2**30:.1f} "
                    f"reserved={torch.cuda.memory_reserved() / 2**30:.1f} GiB"
                    f" dit_parked={parked_dit}"
                )
            try:
                return orig(x)
            except torch.OutOfMemoryError as exc:
                print(f"[vae] decode OOM ({exc}); purging and retrying once")
                gc.collect()
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
                try:
                    return orig(x)
                except torch.OutOfMemoryError as retry_exc:
                    raise RuntimeError(
                        "VAE decode exhausted VRAM at this resolution. Lower width/height "
                        "(1536 is safe with the DiT resident) or generate fewer images."
                    ) from retry_exc
            finally:
                torch.cuda.empty_cache()
                if parked_dit and not self._defer_dit_reload:
                    self.dit.to("cuda")
                    torch.cuda.empty_cache()

        self.ae.decode = decode

    def _clear_custom(self):
        """Unwrap every adapter, restoring the untouched Linear modules."""
        for parent, attr, original in reversed(getattr(self, "_custom_refs", [])):
            setattr(parent, attr, original)
        self._custom_refs = []
        self._custom = None

    def _apply_custom(self, stack: list[tuple[str, float]]) -> int:
        """Attach one or more user LoRAs as stacked adapters; returns wrapped layers."""
        import torch
        from safetensors.torch import load_file

        stack = [(n, round(float(s), 3)) for n, s in stack if n]
        if not stack:
            if self._custom:
                print("[style] removing custom LoRAs")
                self._clear_custom()
            return 0
        key = tuple(stack)
        if self._custom == key:
            return len(self._custom_refs)

        self._clear_custom()
        states: dict[str, dict] = {}
        for name, _scale in stack:
            spec = _lora_catalog().get(name)
            if not spec:
                raise RuntimeError(f"unknown LoRA '{name}' — see /api/info custom_loras")
            path = os.path.join(W, LORA_DIR, spec["file"])
            if not os.path.exists(path):
                _reload_loras(max_age=0.0)  # another container may have committed it
            if not os.path.exists(path):
                # The server fetches it itself; a token is only needed when Civitai gates
                # the file, and that error explains exactly what to set.
                print(f"[style] '{name}' not in the volume yet — downloading")
                try:
                    _fetch_lora(name)
                except Exception as exc:
                    raise RuntimeError(f"{name} is unavailable: {exc}") from exc
            states[name] = load_file(path, device="cpu")

        # Group every LoRA's pairs by module, so a layer touched by two adapters gets one
        # wrapper that sums both instead of a wrapper nested inside a wrapper.
        per_module: dict[str, list] = {}
        counts: dict[str, int] = {}
        for name, scale in stack:
            pairs = _normalize_lora_keys(states[name])
            counts[name] = len(pairs)
            for module_path, (a, b, alpha) in pairs.items():
                eff = float(alpha) / a.shape[0] if alpha else 1.0
                per_module.setdefault(module_path, []).append(
                    (a.to("cuda", torch.float32), b.to("cuda", torch.float32), scale * eff)
                )

        applied = 0
        for module_path, adapters in per_module.items():
            parent, attr = _resolve_module(self.dit, module_path)
            if parent is None or not hasattr(parent, attr):
                continue
            base = getattr(parent, attr)
            if not isinstance(base, torch.nn.Linear):
                continue
            self._custom_refs.append((parent, attr, base))
            setattr(parent, attr, _make_custom_linear(base, adapters))
            applied += 1
        if applied == 0:
            # Name the layout that was found: "nothing matched" alone reads like a library
            # bug, while "transformer.text_fusion.layerwise_blocks" is obviously another model.
            self._custom = None
            found = "; ".join(f"{n}: {_key_prefixes(states[n])}" for n, _ in stack)
            raise RuntimeError(
                f"'{', '.join(n for n, _ in stack)}' loaded but none of its keys match this "
                f"DiT (expects blocks.N.attn.wq/wk/wv/wo/gate and mlp.gate/up/down). Found "
                f"{found} — that layout belongs to a different architecture."
            )
        del states
        self._custom = key
        label = " + ".join(f"{n} @ {s:.2f}" for n, s in stack)
        print(
            f"[style] {label} · {applied} layers adapted "
            f"({', '.join(f'{n}: {counts[n]} pairs' for n, _ in stack)})"
        )
        torch.cuda.empty_cache()
        return applied

    def _to_tensor(self, im):
        import numpy as np
        import torch

        arr = np.asarray(im, dtype=np.float32) / 127.5 - 1.0
        return torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0).to("cuda", torch.bfloat16)

    def _refine_tiles(self, image, target_long: int, prompt: str, negative: str, seed: int):
        """4K: Lanczos upscale, then re-denoise overlapping tiles at low strength.

        Straight generation at 4096 is 22k tokens — out of budget and out of training
        distribution. Instead each 1536 px tile is re-denoised with its true spatial
        offset, so neighbouring tiles agree about where they sit in the full frame, and
        the overlaps are cosine-feathered. That is where the extra detail comes from.
        """
        import numpy as np
        from PIL import Image

        from sampling import sample

        tile, ov = UPSCALE_TILE, 192
        w, h = image.size
        k = target_long / max(w, h)
        W2 = max(tile, int(round(w * k / 16.0)) * 16)
        H2 = max(tile, int(round(h * k / 16.0)) * 16)
        big = image.resize((W2, H2), Image.LANCZOS)
        tw, th = min(tile, W2), min(tile, H2)

        def axis(total: int, size: int):
            if size >= total:
                return [0]
            step = size - ov
            stops = list(range(0, total - size + 1, step))
            if stops[-1] != total - size:
                stops.append(total - size)
            return stops

        xs, ys = axis(W2, tw), axis(H2, th)
        self._encoder_to("cuda")
        txt = self.encoder([prompt])
        neg = self.encoder([negative or ""])

        canvas = np.asarray(big, dtype=np.float32)
        acc = np.zeros_like(canvas)
        wsum = np.zeros((H2, W2, 1), dtype=np.float32)
        ramp = (0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, ov))).astype(np.float32)

        print(f"[4k] {W2}x{H2} from {w}x{h} · {len(xs)}x{len(ys)} tiles of {tw}px")
        for i, y0 in enumerate(ys):
            for j, x0 in enumerate(xs):
                crop = big.crop((x0, y0, x0 + tw, y0 + th))
                lat = self.ae.encode(self._to_tensor(crop))
                out = sample(
                    self.dit,
                    self.ae,
                    self.encoder,
                    [prompt],
                    init_latents=lat,
                    strength=UPSCALE_STRENGTH,
                    steps=UPSCALE_STEPS,
                    guidance=0.0,
                    seed=seed + i * 13 + j,
                    width=tw,
                    height=th,
                    y1=0.5,
                    y2=1.15,
                    mu=1.15,
                    text=txt,
                    negative_text=neg,
                    offset=(y0 // 16, x0 // 16),
                )[0]
                arr = np.asarray(out, dtype=np.float32)
                m = np.ones((th, tw), dtype=np.float32)
                if y0 > 0:
                    m[:ov, :] *= ramp[:, None]
                if y0 + th < H2:
                    m[-ov:, :] *= ramp[::-1][:, None]
                if x0 > 0:
                    m[:, :ov] *= ramp[None, :]
                if x0 + tw < W2:
                    m[:, -ov:] *= ramp[::-1][None, :]
                acc[y0 : y0 + th, x0 : x0 + tw] += arr * m[..., None]
                wsum[y0 : y0 + th, x0 : x0 + tw] += m[..., None]
                del out, arr
        comp = np.clip(acc / np.maximum(wsum, 1e-6), 0, 255).astype(np.uint8)
        return Image.fromarray(comp)

    def _encode_init(self, b64: str, width: int, height: int):
        """Base64 image -> latent on the sampler's grid (img2img's starting point)."""
        import numpy as np
        import torch
        from PIL import Image, ImageOps

        payload = b64.split(",", 1)[-1]
        raw = base64.b64decode(payload)
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
        im = im.resize((width, height), Image.LANCZOS)
        arr = np.asarray(im, dtype=np.float32) / 127.5 - 1.0
        x = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0).to("cuda", dtype=torch.bfloat16)
        with torch.inference_mode():
            return self.ae.encode(x)

    def _enhance(self, prompt: str) -> str:
        """Krea's own prompt-expansion system prompt, run on the already-loaded Qwen3-VL."""
        import torch

        self._encoder_to("cuda")
        with open("/root/krea2/docs/expansion.txt", "r", encoding="utf-8") as f:
            system = f.read()
        tok = self.encoder.tokenizer
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        text = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        enc = tok([text], return_tensors="pt", truncation=True, max_length=2048).to("cuda")
        with torch.inference_mode():
            out = self.encoder.qwen.generate(
                **enc, max_new_tokens=320, do_sample=True, temperature=0.7, top_p=0.9
            )
        new_tokens = out[0][enc["input_ids"].shape[1] :]
        expanded = tok.decode(new_tokens, skip_special_tokens=True).strip()
        parts = [p.strip() for p in expanded.split("\n\n") if p.strip()]
        if not parts:
            return prompt
        best = parts[-1]
        for prefix in ("Expanded prompt:", "Prompt:", "**Prompt:**"):
            if best.startswith(prefix):
                best = best[len(prefix) :].strip()
        return best or prompt

    @modal.method()
    def talk(self, req: dict) -> dict:
        """One chat turn on the unfiltered Qwen3-VL, optionally reading an image.

        The text bridge that conditions renders also answers conversation, so a character
        speaks with the core's own voice instead of a second model's — and a second 4B on
        the same card is 8 GB the sampler would rather have.
        """
        import torch
        from PIL import Image
        from transformers import AutoProcessor

        self._ensure(req.get("variant") or "uncensored", req.get("encoder") or "abliterated")
        self._encoder_to("cuda")
        if getattr(self, "chat_processor", None) is None:
            try:
                # The weights are abliterated; the vocabulary is Qwen3-VL's stock processor,
                # which is exactly where the render path's tokenizer patch points.
                self.chat_processor = AutoProcessor.from_pretrained(ENCODERS["stock"])
            except Exception as exc:  # missing vision deps must not break plain chat
                print(f"[chat] processor unavailable, text-only: {exc}")
                self.chat_processor = False
        processor = self.chat_processor

        chat = []
        for msg in req.get("messages") or []:
            role = "assistant" if str(msg.get("role")) == "assistant" else "user"
            if role == "system":
                continue
            text = str(msg.get("text") or "").strip()
            if text:
                chat.append({"role": role, "content": [{"type": "text", "text": text}]})

        image = str(req.get("image") or "").strip()
        if image:
            if not processor:
                return {"text": "", "error": "vision processor unavailable"}
            # A vision request replaces the conversation: one image, one question.
            raw = base64.b64decode(image.split(",", 1)[-1])
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            im.thumbnail((1024, 1024))
            prompt = str(req.get("vision_prompt") or "Describe what you see.")
            chat = [{"role": "user", "content": [{"type": "image", "image": im},
                                                     {"type": "text", "text": prompt}]}]
        system = str(req.get("system") or "").strip()
        if system and not image:
            chat = [{"role": "system", "content": [{"type": "text", "text": system}]}] + chat
        if not chat:
            return {"text": ""}

        max_new = int(req.get("max_new_tokens") or 280)
        sample = bool(req.get("sample", True))
        temp = float(req.get("temperature") or 0.8)

        if processor:
            inputs = processor.apply_chat_template(
                chat, tokenize=True, add_generation_prompt=True, return_dict=True,
                return_tensors="pt",
            ).to("cuda")
            with torch.inference_mode():
                out = self.encoder.qwen.generate(
                    **inputs, max_new_tokens=max_new, do_sample=sample,
                    temperature=temp, top_p=0.92,
                )
            new = out[:, inputs["input_ids"].shape[1]:]
            return {"text": processor.batch_decode(new, skip_special_tokens=True)[0].strip()}

        # Text-only fallback: the render path's own tokenizer, same chat template.
        tok = self.encoder.tokenizer
        convo = [{"role": "system", "content": system}] if system else []
        convo += [{"role": m["role"], "content": m["content"][0]["text"]} for m in chat]
        prompt = tok.apply_chat_template(convo, tokenize=False, add_generation_prompt=True)
        enc = tok([prompt], return_tensors="pt", truncation=True, max_length=6144).to("cuda")
        with torch.inference_mode():
            out = self.encoder.qwen.generate(
                **enc, max_new_tokens=max_new, do_sample=sample, temperature=temp, top_p=0.92,
            )
        new = out[0][enc["input_ids"].shape[1]:]
        return {"text": tok.decode(new, skip_special_tokens=True).strip()}

    @modal.method()
    def upsell_gallery(self, req: dict) -> dict:
        """Her upgraded personal-gallery frames: recognise, then img2img at 2K (4K on ask).

        The VLM reads the source photo so the prompt can lock the face, hair and wardrobe
        she is known by; img2img at low strength then changes only light, lens and finish.
        That is what makes the result usable as *her* page image instead of a lookalike.
        """
        sources = [str(s) for s in (req.get("sources") or []) if str(s)]
        if not sources:
            return {"error": "sources are required"}
        # `.local()` runs in this container: a bare `self.talk(...)` returns a Function
        # handle in Modal 1.x and dies with "'Function' object is not callable".
        seen = self.talk.local({
            "image": sources[0],
            "temperature": 0.2,
            "max_new_tokens": 200,
            "vision_prompt": (
                "Describe this woman for an image prompt: face, hair, skin, build, wardrobe, "
                "expression and setting. Be literal and specific, two sentences."
            ),
        })
        if seen.get("error"):
            return {"error": str(seen["error"])}
        description = str(seen.get("text") or "").strip()
        prompt = orbit_upsell.identity_prompt(description, str(req.get("scene") or ""))
        seed = int(req.get("seed") or 7)
        strength = float(req.get("strength") or 0.45)

        t0 = time.time()
        renders: dict = {}
        adapters: list = []
        for name in ("thumb", "zoom"):
            long_edge = orbit_upsell.SIZES[name]
            got = self.generate.local({
                "prompt": prompt,
                "init_image": sources[0],
                "strength": strength,
                "width": long_edge * 4 // 5 // 16 * 16,
                "height": long_edge,
                "steps": int(req.get("steps") or 8),
                "cfg": 0,
                "num_images": 1,
                "seed": seed,
                "lora": False,
                # Her page and her generator render through the same adapter, so the gallery
                # frame and the studio frame are the same woman.
                "character": str(req.get("sku") or ""),
            })
            if got.get("error"):
                return {"error": str(got["error"])}
            first = (got.get("images") or [{}])[0]
            # `generate` reports seed/seconds per image, not at the top level. The adapter is
            # reported too, so a caller can prove these frames came through her weights.
            used = got.get("style_loras") or []
            if used and not adapters:
                adapters = used
            renders[name] = {"size": orbit_upsell.SIZES[name], "data": first.get("data") or "",
                             "seed": first.get("seed"), "seconds": first.get("seconds"),
                             "adapters": used}

        if req.get("download_4k") and renders.get("zoom", {}).get("data"):
            got = self.generate.local({
                "prompt": prompt,
                "init_image": renders["zoom"]["data"],
                "strength": 0.3,
                "width": 1632,
                "height": 2048,
                "steps": 8,
                "cfg": 0,
                "num_images": 1,
                "seed": seed,
                "lora": False,
                # Same adapter as her 1K/2K frames and her studio renders.
                "character": str(req.get("sku") or ""),
                "upscale": orbit_upsell.SIZES["download"],
            })
            if not got.get("error"):
                first = (got.get("images") or [{}])[0]
                renders["download"] = {"size": orbit_upsell.SIZES["download"],
                                       "data": first.get("data") or "",
                                       "seed": first.get("seed"), "seconds": first.get("seconds"),
                                       "adapters": got.get("style_loras") or []}
        return {"description": description, "prompt": prompt, "renders": renders,
                "adapters": adapters, "sizes": orbit_upsell.tiers(),
                "seconds": round(time.time() - t0, 1)}

    # -- api ---------------------------------------------------------------------

    @modal.method()
    def info(self) -> dict:
        import torch

        return {
            "gpu": torch.cuda.get_device_name(0),
            "vram_gb": round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1),
            "loaded": list(self.key) if self.key else None,
            "dit_variants": {k: v["label"] for k, v in DITS.items()},
            "encoders": list(ENCODERS),
            "custom_loras": _lora_catalog(),
            "video_loras": _video_lora_catalog(),
            # What every render wears beyond her own adapter; the page shows it read-only.
            "studio_defaults": _studio_config().get("loras") or [],
        }

    @modal.method()
    def generate(self, req: dict) -> dict:
        import random

        # Fixed pair, not a menu: the unfiltered core with the unfiltered bridge is the
        # product, so a request can no more select the stock/filtered pair than it can select
        # a sampling schedule. The page no longer offers either choice.
        variant = "uncensored"
        encoder_kind = "abliterated"

        prompt = (req.get("prompt") or "").strip()[:6000]
        if not prompt:
            return {"error": "prompt is required"}

        width = _snap(_clamp(int(req.get("width") or 1024), 512, 2048))
        height = _snap(_clamp(int(req.get("height") or 1024), 512, 2048))
        # Auto-configured, not a preference: the core is distilled for 8 steps at CFG 0, and
        # any other schedule is slower for no gain. The request cannot change it.
        steps = 8
        cfg = 0.0
        num = _clamp(int(req.get("num_images") or 1), 1, 4)
        seed = int(req.get("seed", -1))
        base_seed = seed if seed >= 0 else random.SystemRandom().randrange(0, 2**31 - num)
        # The negative prompt is fixed, never the caller's: accepting it would let a request
        # strip every protection in `orbit_guard` with one field. Age rule is enforced here
        # too, so a refusing prompt costs no sampling and no GPU work beyond the container.
        negative = orbit_guard.negative_for(req.get("negative_prompt"))
        reason = orbit_guard.refused(req.get("prompt") or "")
        if reason:
            return {"error": f"refused: {reason}"}
        strength = _clamp(float(req.get("strength") or 0.6), 0.05, 1.0)
        init_b64 = (req.get("init_image") or "").strip()
        # The adapter is a property of the profile, never of the request. `style_lora` and
        # `style_loras` are ignored outright: a client that names its own weights could
        # borrow another woman's face, and the page no longer offers the choice at all.
        character = str(req.get("character") or "").strip()
        catalog = _lora_catalog()
        if character and not orbit_studio.pick(catalog, character):
            # The catalog caches the volume listing for a minute, and a fit promoted
            # seconds ago is exactly the case that matters: a warm worker would render
            # the house pair and log "no adapter" for a woman whose weights are right
            # there. Force one re-read before giving up.
            _reload_loras(0.0)
            catalog = _lora_catalog()
        if not orbit_studio.pick(catalog, character):
            print(f"[studio] no adapter for {character!r}; house adapters only")
        # Her weights first so her face wins, then the admin's house pair every render wears.
        stack = [(row["name"], row["scale"])
                 for row in orbit_studio.blend(catalog, character, _studio_config().get("loras"))]
        style_lora = stack[0][0] if stack else ""
        style_scale = stack[0][1] if stack else 0.0
        style_triggers = [
            t
            for t in ((CUSTOM_LORAS.get(n, {}).get("trigger") or "").strip() for n, _ in stack)
            if t
        ]
        for trigger in style_triggers:
            # Some concept LoRAs only fire on their trained word, so the registry may
            # carry one; add it unless the prompt already says it.
            if trigger.lower() not in prompt.lower():
                prompt = f"{trigger} {prompt}"
        upscale = _clamp(int(req.get("upscale") or 0), 0, 4096)

        t0 = time.time()
        self._ensure(variant, encoder_kind)
        load_time = time.time() - t0
        # Decoding parks the text encoder (and, at high res, the DiT) on CPU.
        self._encoder_to("cuda")
        self._dit_to("cuda")

        init_latents = None
        if init_b64:
            init_latents = self._encode_init(init_b64, width, height)
            print(f"[img2img] init {width}x{height} · strength {strength:.2f}")

        style_applied = self._apply_custom(stack)

        # A per-image adapter is fitted on every result: it is the archive that makes her next
        # render consistent, not something a visitor chooses. The request cannot switch it off.
        want_lora = True
        lora_jobs: list[dict] = []
        final_prompt = prompt
        enhanced = False
        # Krea 2's prompt prior is weak, so the expansion pass is part of rendering, not a
        # preference: every render expands, and a request can no longer switch it off.
        try:
            final_prompt = self._enhance(prompt)
            enhanced = final_prompt != prompt
        except Exception as exc:
            print("[enhance] failed, using raw prompt:", exc)
        # The expander writes a brand-new sentence, so put every LoRA trigger back in
        # afterwards: the trained words are what make the adapters fire at all.
        for trigger in style_triggers:
            if trigger.lower() not in final_prompt.lower():
                final_prompt = f"{trigger} {final_prompt}"

        from sampling import sample

        # Encode once and share it: the image path and every LoRA fit condition on
        # exactly the same text, and the LoRA worker then needs no text encoder at all.
        self._encoder_to("cuda")
        txt = self.encoder([final_prompt])

        images = []
        for i in range(num):
            s = base_seed + i
            t = time.time()
            self._encoder_to("cuda")
            self._dit_to("cuda")
            # The last image of the batch can leave the DiT on CPU after its decode.
            self._defer_dit_reload = i < num - 1
            img = sample(
                self.dit,
                self.ae,
                self.encoder,
                [final_prompt],
                negative_prompts=[negative],
                width=width,
                height=height,
                steps=steps,
                guidance=cfg,
                seed=s,
                y1=0.5,
                y2=1.15,
                mu=1.15,  # the distilled checkpoint was trained at a fixed mu
                init_latents=init_latents,
                strength=strength,
                text=txt,
            )[0]
            if upscale and upscale > max(img.width, img.height):
                t_up = time.time()
                img = self._refine_tiles(img, upscale, final_prompt, negative, s + 101)
                print(f"[4k] {img.width}x{img.height} finished in {time.time() - t_up:.1f}s")
            buf = io.BytesIO()
            img.save(buf, format="PNG", compress_level=6)

            if want_lora:
                try:
                    call = Krea2LoRA().fit.spawn(
                        {
                            "image": base64.b64encode(buf.getvalue()).decode("ascii"),
                            "text": txt,
                            "seed": s,
                            "prompt": final_prompt,
                            "variant": variant,
                            "encoder": encoder_kind,
                        }
                    )
                    lora_jobs.append({"id": call.object_id, "seed": s, "index": i})
                    print(f"[lora] queued for seed={s} ({call.object_id})")
                except Exception as exc:
                    print("[lora] spawn failed:", exc)
            images.append(
                {
                    "data": base64.b64encode(buf.getvalue()).decode("ascii"),
                    "seed": s,
                    "width": img.width,
                    "height": img.height,
                    "seconds": round(time.time() - t, 2),
                }
            )
            print(f"[gen] seed={s} {img.width}x{img.height} {steps} steps in {images[-1]['seconds']}s")

        return {
            "images": images,
            "prompt": final_prompt,
            "original_prompt": prompt,
            "enhanced": enhanced,
            "variant": variant,
            "text_encoder": encoder_kind,
            "steps": steps,
            "cfg": cfg,
            "width": width,
            "height": height,
            "mode": "img2img" if init_latents is not None else "t2i",
            "strength": strength,
            "lora_jobs": lora_jobs,
            "lora_rank": LORA_RANK if lora_jobs else None,
            "style_lora": style_lora,
            "style_scale": style_scale,
            "style_trigger": ", ".join(style_triggers),
            "style_loras": [{"name": n, "scale": s} for n, s in stack],
            "style_triggers": style_triggers,
            "prompt_used": final_prompt,
            "style_adapters": style_applied,
            "upscale": upscale if upscale and upscale > max(width, height) else 0,
            "elapsed": round(time.time() - t0, 2),
            "load_seconds": round(load_time, 2),
        }


# --------------------------------------------------------------------------------------
# Video worker (Wan 2.2 TI2V-5B)
# --------------------------------------------------------------------------------------

@app.cls(
    image=image,
    gpu=VIDEO_GPU,
    volumes={W: weights_vol, HF: hf_vol},
    timeout=1800,
    scaledown_window=VIDEO_SCALEDOWN,
    # A14B is 126 GB on disk and every byte is materialised in host RAM before the
    # experts move to the card; 64 GB gets the container killed mid-load.
    memory=196608,
    max_containers=1,
)
@modal.concurrent(max_inputs=1)
class Krea2Video:
    """Image-to-video, unfiltered.

    Wan 2.2 TI2V-5B with an expanded-timestep VAE: the first frame is your image, so
    the clip continues whatever that frame actually depicts. There is no output
    classifier and no prompt rewrite in this path.
    """

    @modal.enter()
    def boot(self):
        import torch

        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        torch.backends.cudnn.benchmark = True
        self.pipe = None
        self.spec = None
        self._lora = None
        print(
            f"[video-boot] {torch.cuda.get_device_name(0)} · "
            f"{torch.cuda.get_device_properties(0).total_memory / 1e9:.0f} GB"
        )

    def _ensure(self, model: str):
        import gc

        import torch

        spec = VIDEO_MODELS.get(model) or VIDEO_MODELS[DEFAULT_VIDEO_MODEL]
        if self.pipe is not None and self.spec is spec:
            return
        if self.pipe is not None:
            print(f"[video] unloading {self.spec['label']}")
            self.pipe = None
            self.spec = None
            gc.collect()
            torch.cuda.empty_cache()
        from diffusers import AutoencoderKLWan, WanImageToVideoPipeline

        path = os.path.join(W, spec["dir"])
        if not os.path.exists(os.path.join(path, "model_index.json")):
            raise RuntimeError(
                f"video weights missing from {path} — run "
                "`modal run app.py::main --video-download` to populate them"
            )
        weights_vol.reload()
        t0 = time.time()
        # fp32 VAE: Wan's temporal decoder loses the last row of pixels otherwise.
        vae = AutoencoderKLWan.from_pretrained(path, subfolder="vae", torch_dtype=torch.float32)
        pipe = WanImageToVideoPipeline.from_pretrained(path, vae=vae, torch_dtype=torch.bfloat16)
        pipe.set_progress_bar_config(disable=True)
        if spec["moe"]:
            # 28B across two experts cannot sit on one card. Let accelerate own the
            # placement: model-level offload keeps one 28 GB expert resident at a time
            # and — unlike hand-moved modules — leaves `_execution_device` on the GPU,
            # so latents and generators are still built on the right device.
            pipe.enable_model_cpu_offload(device="cuda")
        else:
            pipe.to("cuda")
        self.pipe = pipe
        self.spec = spec
        print(
            f"[video] {spec['label']} loaded in {time.time() - t0:.1f}s · "
            f"vram {torch.cuda.memory_allocated() / 2**30:.1f} GiB"
        )

    def _apply_lora(self, name: str, scale: float) -> int:
        """Attach (or clear) a Wan LoRA on the loaded pipeline; returns adapters used."""
        pipe = self.pipe
        if not name:
            if self._lora:
                print("[video-lora] removing adapter")
                try:
                    pipe.unload_lora_weights()
                except Exception as exc:
                    print(f"[video-lora] unload failed: {type(exc).__name__}: {exc}")
                self._lora = None
            return 0
        if self._lora == (name, round(scale, 3)):
            return 1
        spec = _video_lora_catalog()[name]
        path = os.path.join(W, LORA_DIR, "video", spec["file"])
        if not os.path.exists(path):
            _reload_loras(max_age=0.0)  # another container may have committed it
        if not os.path.exists(path):
            print(f"[video-lora] '{name}' not in the volume yet — downloading")
            _fetch_lora(name)
        if self._lora:
            try:
                pipe.unload_lora_weights()
            except Exception as exc:
                print(f"[video-lora] unload failed: {type(exc).__name__}: {exc}")
            self._lora = None
        # Wan LoRAs are PEFT-format; diffusers reads them straight onto the transformer.
        pipe.load_lora_weights(path, adapter_name=name)
        pipe.set_adapters([name], adapter_weights=[float(scale)])
        self._lora = (name, round(scale, 3))
        print(f"[video-lora] {name} @ {scale:.2f} from {os.path.basename(path)}")
        return 1

    @modal.method()
    def info(self) -> dict:
        import torch

        return {
            "gpu": torch.cuda.get_device_name(0),
            "vram_gb": round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1),
            "loaded": self.spec["label"] if self.spec else None,
            "lora": self._lora[0] if self._lora else None,
            "video_loras": _video_lora_catalog(),
            "models": {k: {
                "label": v["label"],
                "weights": os.path.exists(os.path.join(W, v["dir"], "model_index.json")),
            } for k, v in VIDEO_MODELS.items()},
        }

    @modal.method()
    def generate(self, req: dict) -> dict:
        import random

        import torch
        from diffusers.utils import export_to_video
        from PIL import Image

        raw_b64 = (req.get("init_image") or "").strip()
        if not raw_b64:
            return {"error": "video is image-to-video: an init image is required"}
        prompt = (req.get("prompt") or "").strip()[:4000]
        if not prompt:
            return {"error": "prompt is required"}

        model = req.get("model") or DEFAULT_VIDEO_MODEL
        if model not in VIDEO_MODELS:
            model = DEFAULT_VIDEO_MODEL

        # Validate the LoRA before spending two minutes loading 126 GB of expert weights.
        lora_name = (req.get("lora") or "").strip()
        lora_scale = _clamp(float(req.get("lora_scale") or 1.0), 0.0, 2.0)
        lora_trigger = ""
        if lora_name:
            spec = _video_lora_catalog().get(lora_name)
            if not spec:
                return {"error": f"unknown video LoRA '{lora_name}' — see /api/info video_loras"}
            allowed = spec.get("models")
            if allowed is not None and model not in allowed:
                return {
                    "error": f"'{lora_name}' is trained for {spec['base'] or 'another base'}, "
                    f"but this request uses {model}. Pick a version trained for Wan 2.2, "
                    "or generate with that other pipeline elsewhere."
                }
            lora_trigger = (spec.get("trigger") or "").strip()
            if not lora_scale:
                lora_scale = float(spec.get("default_scale") or 1.0)
            if lora_trigger and lora_trigger.lower() not in prompt.lower():
                prompt = f"{lora_trigger}, {prompt}"

        if lora_name:
            # Fetch before the 126 GB load: a gated or missing file should fail in seconds
            # rather than after the pipeline is already resident.
            lora_path = os.path.join(W, LORA_DIR, "video", _video_lora_catalog()[lora_name]["file"])
            if not os.path.exists(lora_path):
                _reload_loras(max_age=0.0)  # another container may have committed it
            if not os.path.exists(lora_path):
                try:
                    print(f"[video-lora] '{lora_name}' not in the volume yet — downloading")
                    _fetch_lora(lora_name)
                except Exception as exc:
                    return {"error": f"{type(exc).__name__}: {exc}"[:600]}

        t0 = time.time()
        self._ensure(model)
        pipe = self.pipe
        moe = bool(self.spec["moe"])
        try:
            self._apply_lora(lora_name, lora_scale)
        except Exception as exc:
            # A missing or undownloadable LoRA is a bad request, not a server fault.
            return {"error": f"{type(exc).__name__}: {exc}"[:600]}

        raw = base64.b64decode(raw_b64.split(",", 1)[-1])
        im = Image.open(io.BytesIO(raw)).convert("RGB")

        # Wan is native at 832-wide (480p) and 1280-wide (720p).
        quality = 720 if int(req.get("quality") or 480) >= 720 else 480
        num_frames = int(req.get("frames") or 81)
        num_frames = max(17, min(161, (num_frames - 1) // 4 * 4 + 1))
        # Auto-configured for Wan 2.2: 24 steps at guidance 5 is the measured sweet spot on
        # this card, and exposing it only invited slower, worse clips.
        steps = 24
        guidance = 5.0
        fps = _clamp(int(req.get("fps") or 24), 8, 30)
        seed = int(req.get("seed", -1))
        seed = seed if seed >= 0 else random.SystemRandom().randrange(0, 2**31 - 1)
        # The negative prompt is fixed, never the caller's: accepting it would let a request
        # strip every protection in `orbit_guard` with one field. Age rule is enforced here
        # too, so a refusing prompt costs no sampling and no GPU work beyond the container.
        negative = orbit_guard.negative_for(req.get("negative_prompt"))
        reason = orbit_guard.refused(req.get("prompt") or "")
        if reason:
            return {"error": f"refused: {reason}"}

        # 57 GB of resident expert leaves less room for attention than the 5B does.
        if moe and quality == 720 and num_frames > 49:
            print("[video] A14B: 720p capped at 49 frames on 80 GB — using 480p")
            quality = 480

        long_side = 1280 if quality == 720 else 832
        ratio = im.width / max(1, im.height)
        if ratio >= 1:
            width, height = long_side, int(round(long_side / ratio))
        else:
            width, height = int(round(long_side * ratio)), long_side
        width, height = _snap32(width), _snap32(height)
        budget = 1280 * 704
        if width * height > budget:
            k = (budget / (width * height)) ** 0.5
            width, height = _snap32(width * k), _snap32(height * k)

        im = im.resize((width, height), Image.LANCZOS)
        gen = torch.Generator(device="cuda").manual_seed(seed)

        # Deliberately keep the T5 on the card: diffusers derives `_execution_device`
        # from the first module in `components`, so a CPU text_encoder makes the whole
        # pipeline believe it runs on CPU and the latents are then built on the wrong
        # device ("cannot generate a cuda tensor from a cpu generator"). One A14B expert
        # (28 GB bf16) + T5 (5.5 GB) + VAE fits, so nothing needs parking.

        t = time.time()
        out = pipe(
            image=im,
            prompt=prompt,
            negative_prompt=negative,
            height=height,
            width=width,
            num_frames=num_frames,
            num_inference_steps=steps,
            guidance_scale=guidance,
            generator=gen,
            output_type="pil",
        )
        frames = out.frames[0]
        path = f"/tmp/krea2_{seed}.mp4"
        export_to_video(frames, path, fps=fps)
        with open(path, "rb") as f:
            data = f.read()

        thumb = frames[-1].copy()
        tw = min(540, thumb.width)
        thumb = thumb.resize((tw, max(1, round(thumb.height * tw / thumb.width))), Image.LANCZOS)
        buf = io.BytesIO()
        thumb.save(buf, format="JPEG", quality=86)

        # The last frame is the chain link: feeding it back in continues the clip.
        chain = frames[-1].copy()
        cbuf = io.BytesIO()
        chain.save(cbuf, format="JPEG", quality=94)

        seconds = round(time.time() - t, 2)
        print(
            f"[video] {self.spec['dir']} {width}x{height} · {num_frames}f · {steps} steps · "
            f"{seconds}s · {len(data) / 1e6:.1f} MB"
        )
        del out, frames
        torch.cuda.empty_cache()

        return {
            "video": base64.b64encode(data).decode("ascii"),
            "thumb": base64.b64encode(buf.getvalue()).decode("ascii"),
            "last_frame": base64.b64encode(cbuf.getvalue()).decode("ascii"),
            "model": self.spec["label"],
            "model_id": self.spec["dir"],
            "mime": "video/mp4",
            "seed": seed,
            "width": width,
            "height": height,
            "frames": num_frames,
            "fps": fps,
            "steps": steps,
            "cfg": guidance,
            "seconds": seconds,
            "prompt": prompt,
            "lora": lora_name,
            "lora_scale": lora_scale,
            "lora_base": (_video_lora_catalog()[lora_name]["base"] if lora_name else None),
            "elapsed": round(time.time() - t0, 2),
        }


# --------------------------------------------------------------------------------------
# LoRA worker: one short fit per generated image, on its own container
# --------------------------------------------------------------------------------------


# How the artifacts are named on SeaArt. Its LoRA uploader identifies a LoRA by base model
# alone — there is no separate text-encoder or VAE slot — so this is what a user needs to
# pick, and why the exported .safetensors needs no rename to be recognised there.
SEAART = {
    "base_model": "Krea 2",
    "base_page": "https://www.seaart.ai/model/krea-2",
    "lora_type": "LORA",
}


def _lora_notes(*, seed: int, variant: str, encoder: str, steps: int, rank: int, size: int,
                losses: list, seconds: float, prompt: str, width: int, height: int,
                stored: str) -> str:
    """Sidecar .txt saved beside every per-image LoRA: what it is, how it was trained,
    and what each model is called where the file gets uploaded."""
    dit = DITS.get(variant) or DITS["uncensored"]
    repo = ENCODERS.get(encoder, ENCODERS["abliterated"])
    first = losses[0] if losses else None
    last = losses[-1] if losses else None
    return f"""Krea 2 per-image LoRA — training and model details
==================================================
created (UTC)   : {time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
weights file    : {stored}
size            : {size} bytes ({size / 2**20:.1f} MiB)
seed            : {seed}
prompt          : {prompt}

TRAINING
steps           : {steps}
epochs          : {steps}  (one image at batch 1, so each step is a full pass over it)
optimizer       : AdamW, learning rate {LORA_LR:g}, weight decay 0
loss (MSE)      : first {first} -> last {last}
timesteps       : flow-match noise fraction 0.2 -> 0.9 across the run
latent image    : {width}x{height} px, encoded with the Qwen-Image VAE (16 channels)
wall clock      : {seconds}s

LORA LAYOUT
rank / alpha    : {rank} / {rank}
layers          : 28 blocks x 8 modules = 224 wrapped Linears
modules         : attn.wq, attn.wk, attn.wv, attn.wo, attn.gate,
                  mlp.gate, mlp.up, mlp.down
tensors         : 448 keys, all prefixed "diffusion_model."
loader metadata : ss_network_module=networks.lora, ss_network_dim={rank},
                  ss_network_alpha={rank}, ss_base_model_version=krea2-{variant},
                  modelspec.title="krea2 seed {seed}"

MODELS USED
diffusion (DiT) : {dit['local']}
                  variant "{variant}" — {dit['label']}
                  repo {dit['repo']}
                  source {dit['file']}
text encoder    : Qwen3-VL-4B, 12-layer tap ({', '.join(str(n) for n in SELECT_LAYERS)})
                  {repo}
vae             : Qwen-Image VAE, used only to encode the image for the fit

IN THIS APP
sampling        : 8 steps, guidance 0.0 (CFG off), euler + simple, 1024x1024 default
usage           : copy the .safetensors into the volume's loras/ folder and name it as
                  "style_lora"; loras/per_image/ is an archive, not a registry entry.
                  Nothing was trained as a trigger word — this is fitted to one image and
                  reads strongest around scale 0.8-1.0.

SEAART
base model      : {SEAART['base_model']}  ({SEAART['base_page']})
upload as       : {SEAART['lora_type']}, Base Model "{SEAART['base_model']}"
how it is ID'd  : SeaArt identifies a LoRA by its base model only — there is no separate
                  text-encoder or VAE choice, they ship with the base — so the
                  .safetensors needs no rename to be recognised.
sampler there   : 8 steps, guidance 0.0, euler; LoRA weight 0.8-1.0
"""


@app.cls(
    image=image,
    gpu=GPU,
    volumes={W: weights_vol, HF: hf_vol},
    timeout=1800,
    scaledown_window=SCALEDOWN_WINDOW,
    memory=65536,
    max_containers=1,
)
@modal.concurrent(max_inputs=1)
class Krea2LoRA:
    """Fit a small adapter on a single generated image and serialise it.

    A LoRA cannot be read out of an inference pass — the weights never change — so this
    is a real (short) fit: the image is VAE-encoded, re-noised at increasing timesteps,
    and the adapter is trained to predict the flow-matching velocity for that image under
    the same text embedding the image was generated from. It runs in its own container,
    so the image returns while this is still going.
    """

    @modal.enter()
    def boot(self):
        import torch

        self.dit = None
        self.ae = None
        self.key = None
        print(f"[lora-boot] {torch.cuda.get_device_name(0)}")

    def _ensure(self, variant: str):
        import gc
        import sys

        import torch

        if self.key == variant:
            return
        self.dit = self.ae = None
        gc.collect()
        torch.cuda.empty_cache()

        sys.path.insert(0, "/root/krea2")
        from autoencoder import QwenAutoencoder
        from mmdit import SingleMMDiTConfig, SingleStreamDiT
        from safetensors.torch import load_file

        weights_vol.reload()
        t0 = time.time()
        materialized = MATERIALIZED.get(variant)
        path = os.path.join(W, materialized) if materialized else ""
        if not (path and os.path.exists(path)):
            path = os.path.join(W, DITS[variant]["local"])
        state = load_file(path, device="cpu")
        if any(k.endswith("_scale") for k in state):
            state = _dequant(state, torch)
        for k in list(state):
            v = state[k]
            if v.ndim <= 1 and v.dtype != torch.float32:
                state[k] = v.float()

        with torch.device("meta"):
            dit = SingleStreamDiT(SingleMMDiTConfig(**DIT_CONFIG))
        dit.load_state_dict(state, strict=True, assign=True)
        del state
        gc.collect()
        self.dit = dit.to("cuda", torch.bfloat16).eval()
        self.ae = QwenAutoencoder().to("cuda", torch.bfloat16).eval()
        for p in self.dit.parameters():
            p.requires_grad_(False)
        adapters = _inject_lora(self.dit, LORA_RANK)
        for name, p in self.dit.named_parameters():
            if "lora_" in name:
                p.requires_grad_(True)
        self.key = variant
        print(
            f"[lora] {variant} ready in {time.time() - t0:.1f}s · "
            f"{adapters} adapters rank {LORA_RANK}"
        )

    @modal.method()
    def fit(self, req: dict) -> dict:
        import numpy as np
        import torch
        from einops import rearrange
        from PIL import Image, ImageOps
        from safetensors.torch import save_file
        from sampling import prepare

        variant = req.get("variant") or "uncensored"
        encoder_kind = req.get("encoder") or "abliterated"
        self._ensure(variant)
        # Modal serialises the embedding over the wire, so it arrives on CPU.
        txt, txtmask = req["text"]
        txt = txt.to("cuda", torch.bfloat16)
        txtmask = txtmask.to("cuda")
        seed = int(req.get("seed") or 0)

        raw = base64.b64decode(req["image"])
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
        w, h = img.size
        k = min(1.0, LORA_MAXSIDE / max(w, h))
        tw = max(256, int(w * k) // 16 * 16)
        th = max(256, int(h * k) // 16 * 16)
        img = img.resize((tw, th), Image.LANCZOS)

        arr = np.asarray(img, dtype=np.float32) / 127.5 - 1.0
        x0 = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0).to("cuda", torch.bfloat16)
        with torch.inference_mode():
            lat = self.ae.encode(x0)
        del x0

        patch = self.dit.config.patch
        params = [p for n, p in self.dit.named_parameters() if "lora_" in n and p.requires_grad]
        if not params:
            raise RuntimeError(
                "no trainable LoRA parameters — adapter injection did not attach "
                "(check that block.attn.wq/wv/wo and block.mlp.down are still Linear)"
            )
        opt = torch.optim.AdamW(params, lr=LORA_LR, weight_decay=0.0)
        gen = torch.Generator(device="cuda").manual_seed(seed)

        t0 = time.time()
        losses: list[float] = []
        for step in range(LORA_STEPS):
            noise = torch.randn(lat.shape, generator=gen, device="cuda", dtype=torch.bfloat16)
            frac = 0.2 + 0.7 * (step / max(1, LORA_STEPS - 1))
            xt = lat * (1 - frac) + noise * frac
            tokens, pos, mask = prepare(xt, txt.shape[1], patch, txtmask)
            target = rearrange(
                noise - lat, "b c (h ph) (w pw) -> b (h w) (c ph pw)", ph=patch, pw=patch
            )
            t = torch.full((1,), frac, device="cuda", dtype=torch.bfloat16)
            pred = self.dit(img=tokens, context=txt, t=t, pos=pos, mask=mask)
            loss = torch.nn.functional.mse_loss(pred.float(), target.float())
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            losses.append(round(float(loss), 6))
            del noise, xt, tokens, pos, mask, target, pred, loss
        torch.cuda.empty_cache()

        state = _lora_tensors(self.dit)
        path = f"/tmp/lora_{seed}.safetensors"
        save_file(
            state,
            path,
            metadata={
                "format": "pt",
                "type": "krea2-image-lora",
                "rank": str(LORA_RANK),
                "alpha": str(LORA_RANK),
                "steps": str(LORA_STEPS),
                "base": variant,
                "seed": str(seed),
                "prompt": (req.get("prompt") or "")[:1000],
                # Loader hints: ComfyUI and the hosted UIs read these instead of guessing
                # the rank from the tensor shapes.
                "ss_network_module": "networks.lora",
                "ss_network_dim": str(LORA_RANK),
                "ss_network_alpha": str(LORA_RANK),
                "ss_base_model_version": f"krea2-{variant}",
                "modelspec.title": f"krea2 seed {seed}",
            },
        )
        with open(path, "rb") as f:
            data = f.read()
        seconds = round(time.time() - t0, 1)

        # Archive the weights and their sidecar .txt in the volume, so every saved LoRA
        # carries its own record instead of it living only in the job response.
        stem = f"krea2_seed{seed}_{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}"
        folder = os.path.join(W, LORA_DIR, "per_image")
        os.makedirs(folder, exist_ok=True)
        stored = f"per_image/{stem}.safetensors"
        with open(os.path.join(W, LORA_DIR, stored), "wb") as f:
            f.write(data)
        notes = _lora_notes(
            seed=seed, variant=variant, encoder=encoder_kind, steps=LORA_STEPS,
            rank=LORA_RANK, size=len(data), losses=losses, seconds=seconds,
            prompt=(req.get("prompt") or "")[:1000], width=tw, height=th, stored=stored,
        )
        with open(os.path.join(folder, stem + ".txt"), "w", encoding="utf-8") as f:
            f.write(notes)
        weights_vol.commit()
        print(f"[lora] saved {stored} + {stem}.txt")

        print(
            f"[lora] seed={seed} {len(data) / 1e6:.1f} MB · {LORA_STEPS} steps · "
            f"{seconds}s · loss {losses[0]} -> {losses[-1]}"
        )
        return {
            "bytes": len(data),
            "notes": notes,
            "stored": stored,
            "rank": LORA_RANK,
            "steps": LORA_STEPS,
            "loss_first": losses[0] if losses else None,
            "loss_last": losses[-1] if losses else None,
            "seconds": seconds,
            "base": variant,
            "seed": seed,
            "data": base64.b64encode(data).decode("ascii"),
        }


@app.function(image=image, cpu=4, memory=8192, timeout=1200)
def stitch(clips: list[str], fps: int = 24) -> dict:
    """Concatenate clips into one mp4, normalising size/fps so mixed lengths still join."""
    import subprocess
    import tempfile
    from pathlib import Path

    import imageio.v2 as iio
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        paths = []
        for i, b64 in enumerate(clips):
            p = d / f"{i:03d}.mp4"
            p.write_bytes(base64.b64decode(b64.split(",", 1)[-1]))
            paths.append(p)
        if not paths:
            return {"error": "no clips"}

        target = None
        for p in paths:
            try:
                rd = iio.get_reader(str(p))
                meta = rd.get_meta_data()
                rd.close()
                size = meta.get("size")
                if size and (target is None or size[0] * size[1] > target[0] * target[1]):
                    target = tuple(size)
            except Exception as exc:
                print(f"[stitch] metadata failed for {p.name}: {exc}")
        target = target or (832, 480)

        parts = []
        for i, p in enumerate(paths):
            out = d / f"n{i:03d}.mp4"
            vf = (
                f"scale={target[0]}:{target[1]}:force_original_aspect_ratio=decrease,"
                f"pad={target[0]}:{target[1]}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
            )
            subprocess.run(
                [ffmpeg, "-y", "-loglevel", "error", "-i", str(p),
                 "-vf", vf, "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast",
                 "-crf", "17", "-pix_fmt", "yuv420p", str(out)],
                check=True, capture_output=True,
            )
            parts.append(out)

        listfile = d / "list.txt"
        listfile.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
        final = d / "final.mp4"
        subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", str(listfile), "-c", "copy", str(final)],
            check=True, capture_output=True,
        )
        data = final.read_bytes()

    print(f"[stitch] {len(paths)} clips -> {target[0]}x{target[1]} {len(data) / 1e6:.2f} MB")
    return {
        "video": base64.b64encode(data).decode("ascii"),
        "clips": len(paths),
        "width": target[0],
        "height": target[1],
        "fps": fps,
        "mb": round(len(data) / 1e6, 2),
    }


# --------------------------------------------------------------------------------------
# Web UI
# --------------------------------------------------------------------------------------

web_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi[standard]>=0.115.0", "pillow>=10.0.0")
    .add_local_file("ui.html", "/root/ui.html", copy=True)
    .add_local_dir("gallery", "/root/gallery", copy=True)
    .add_local_dir("market", "/root/market", copy=True)
    # Modal 1.x does not implicitly mount sibling modules; without this the container
    # crashes on `import orbit_net` and every route answers nothing.
    .add_local_python_source("orbit_net", "orbit_econ", "orbit_market", "orbit_girls",
                            "orbit_culture", "orbit_chat", "orbit_upsell", "orbit_guard",
                            "orbit_studio", "orbit_engine")
)


_ASGI_KWARGS: dict = {"label": "orbital"}
if CUSTOM_DOMAINS:
    _ASGI_KWARGS["custom_domains"] = CUSTOM_DOMAINS


def _engine_preference() -> str:
    """`ENGINE_PREFER` pins a host for a batch. The chooser still refuses it if it loses money."""
    return os.environ.get("ENGINE_PREFER", "").strip()


@app.function(image=web_image, volumes={W: weights_vol}, timeout=1500, secrets=_ENV_SECRETS)
def commission_render(order_id: str) -> dict:
    """Render one paid Velvet order in the background and park the frame on the volume.

    The HUD's POST has to answer inside LSL's ~30 s HTTP window, so the route only records
    the order and spawns this; the client then polls `/api/commission/<id>`. A failure
    reverses the wallet debit exactly once, here, because that is the only place that
    knows the render did not happen.
    """
    import base64
    import io as _io
    import os as _os

    rec = commissions.get(order_id)
    if not isinstance(rec, dict):
        return {"error": "unknown order"}
    if rec.get("status") == "ready":
        return {"ok": True, "status": "ready", "already": True}

    rec["status"] = "generating"
    rec["startedAt"] = int(time.time())
    commissions[order_id] = rec

    def fail(reason) -> dict:
        rec["status"] = "failed"
        rec["error"] = str(reason)[:300]
        rec["finishedAt"] = int(time.time())
        commissions[order_id] = rec
        # The L$ must not survive a failure: put it back in the wallet, once.
        reader = str(rec.get("clientUuid") or "")
        if reader:
            _wallet_add(reader, int(rec.get("priceL") or 0), "refund " + order_id)
        print(f"[commission] {order_id} failed: {rec['error']}")
        return {"ok": False, "status": "failed", "error": rec["error"]}

    # Rendered at the order's own tier. `steps` is deliberately NOT sent: `generate` pins the
    # schedule at 8 steps for this distilled core and ignores the field, so claiming otherwise
    # on a receipt would be a lie. The frame is recorded with the steps the worker reports.
    tier = COMMISSION_RENDER.get(str(rec.get("kind"))) or COMMISSION_RENDER["test"]
    price_usd = float(COMMISSION_USD.get(str(rec.get("kind"))) or 0.0)
    job = {
        "prompt": rec.get("prompt") or "",
        "width": int(tier["width"]), "height": int(tier["height"]),
        "num_images": 1,
        "seed": int(rec.get("seed") or 7),
        "lora": False,              # the client never picks adapters; `character` chooses hers
        "character": str(rec.get("sku") or ""),
        "upscale": int(tier.get("upscale") or 0),
        "kind": str(rec.get("kind") or "test"),
    }
    # Cheapest configured host that still leaves this order profitable. With no provider keys
    # set this is always "modal", so an unconfigured deploy behaves exactly as before.
    engine = orbit_engine.choose(job, price_usd, preferred=_engine_preference())
    got = None
    if engine != "modal":
        try:
            got = orbit_engine.render(job, engine)
            print(f"[commission] {order_id} drawn on {engine}")
        except Exception as exc:
            # A remote host failing is not the client's problem and must never cost them the
            # order: fall through to the local pipeline, which is already warm in the worst case.
            print(f"[commission] {order_id} {engine} failed, falling back to modal: "
                  f"{type(exc).__name__}: {exc}")
            rec["engineFallback"] = f"{engine}: {type(exc).__name__}: {str(exc)[:120]}"
            engine = "modal"
            got = None
    if got is None:
        try:
            got = Krea2().generate.remote(job)
        except Exception as exc:
            return fail(f"{type(exc).__name__}: {exc}")
    if got.get("error"):
        return fail(got["error"])
    first = (got.get("images") or [{}])[0]
    data = str(first.get("data") or "")
    if not data:
        return fail("the renderer returned no image")
    raw = base64.b64decode(data.split(",", 1)[-1])

    folder = _os.path.join(W, COMMISSION_DIR)
    _os.makedirs(folder, exist_ok=True)
    with open(_os.path.join(folder, order_id + ".png"), "wb") as f:
        f.write(raw)
    # A card-sized JPEG beside the master: the view page is opened from Second Life, where
    # a 3 MB PNG over a 4:5 frame is the difference between "delivered" and "still loading".
    try:
        from PIL import Image

        with Image.open(_io.BytesIO(raw)) as im:
            im = im.convert("RGB")
            if im.width > 1280:
                im = im.resize((1280, max(1, round(im.height * 1280 / im.width))), Image.LANCZOS)
            buf = _io.BytesIO()
            im.save(buf, format="JPEG", quality=88, optimize=True)
        with open(_os.path.join(folder, order_id + ".jpg"), "wb") as f:
            f.write(buf.getvalue())
    except Exception as exc:
        print(f"[commission] jpeg skipped for {order_id}: {exc}")
    try:
        weights_vol.commit()
    except Exception as exc:
        print(f"[commission] volume commit failed: {exc}")

    rec["status"] = "ready"
    rec["seed"] = first.get("seed") or rec.get("seed")
    # What the worker actually ran, not what the tier asked for.
    rec["steps"] = got.get("steps") or 0
    rec["width"] = first.get("width") or tier["width"]
    rec["height"] = first.get("height") or tier["height"]
    rec["upscale"] = got.get("upscale") or 0
    rec["seconds"] = first.get("seconds")
    rec["adapters"] = got.get("style_loras") or []
    rec["bytes"] = len(raw)
    rec["engine"] = engine
    # Revenue, cost and margin for this order, in integer cents. Booked only here, on a frame
    # that exists: a refunded order never reaches this line, so the ledger cannot call a
    # failure a sale.
    rec["cost"] = orbit_engine.settle(price_usd, engine,
                                     float(got.get("engineSeconds") or rec.get("seconds") or 0),
                                     len(raw))
    try:
        usage_ledger["engine:" + order_id] = rec["cost"]
    except Exception as exc:
        print(f"[engine] ledger write failed for {order_id}: {exc}")
    rec["finishedAt"] = int(time.time())
    rec["openUrl"] = "/aigf/v/" + order_id
    commissions[order_id] = rec
    print(f"[commission] {order_id} ready · {len(raw) / 1e6:.2f} MB · {rec.get('seconds')}s")
    return {"ok": True, "status": "ready", "bytes": len(raw)}


@app.function(image=web_image, timeout=60, scaledown_window=60)
@modal.asgi_app(label="krea2")
def krea2_alias():
    """Retired `xzyorb--krea2.modal.run` -> the studio's current address.

    That URL belonged to the old `krea2-uncensored` app, which is gone. A label no live
    app owns is answered by Modal itself with a bare 404 (`modal-http: invalid function
    call`), so old bookmarks and links died silently. This claims the label back and
    redirects, keeping the URL working without a second GPU app to pay for.
    """
    from fastapi import FastAPI, Request
    from fastapi.responses import RedirectResponse

    alias = FastAPI(title="JINXAI ORBITAL (moved)")

    @alias.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def _moved(path: str, request: Request):
        target = f"https://{SITE_DOMAIN}/aigf"
        if path:
            target += "/" + path
        if request.url.query:
            target += "?" + request.url.query
        return RedirectResponse(target, status_code=307)

    return alias


@app.function(
    image=web_image,
    volumes={W: weights_vol},
    timeout=1800,
    scaledown_window=600,
    max_containers=3,
    secrets=_ENV_SECRETS,
)
@modal.asgi_app(**_ASGI_KWARGS)
def web():
    from fastapi import Body, FastAPI, HTTPException, Request
    from fastapi.responses import HTMLResponse, JSONResponse

    api = FastAPI(title="JINXAI ORBITAL")
    service = Krea2()
    video = Krea2Video()
    with open("/root/ui.html", "r", encoding="utf-8") as f:
        page = f.read()
    import json as _json

    with open("/root/gallery/catalog.json", "r", encoding="utf-8") as f:
        catalog = _json.load(f)
    with open("/root/gallery/index.html", "r", encoding="utf-8") as f:
        gallery_html = f.read()

    import glob as _glob

    market_seed: list[dict] = []
    for _path in sorted(_glob.glob("/root/market/listings/*.json")):
        try:
            with open(_path, "r", encoding="utf-8") as f:
                market_seed.append(_json.load(f))
        except Exception as exc:
            print(f"[market] skipped {_path}: {exc}")

    def _guard(request: Request) -> None:
        """Gate writes when ORBITAL_ACCESS_KEY is set; a no-op otherwise."""
        if not ACCESS_KEY:
            return
        got = request.headers.get("x-access-key", "")
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            got = auth[7:].strip()
        if got != ACCESS_KEY:
            raise HTTPException(status_code=401, detail="access key required")

    def _via_site_domain(request: Request) -> bool:
        """True when the visitor reached us through the branded domain via Caddy."""
        # Modal's edge rewrites Host, so the visitor's domain arrives in X-Forwarded-Host.
        raw = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
        host = raw.split(",")[0].strip().split(":")[0].lower()
        return bool(SITE_DOMAIN) and (host == SITE_DOMAIN or host.endswith("." + SITE_DOMAIN))

    def _subfolder(request: Request) -> str:
        """The subfolder this request arrived through, or "" for a direct caller.

        Caddy sets `X-Orbital-Prefix` on its `/aigf` proxies; the host check is the
        fallback for a proxy that forwards only the visitor's hostname.
        """
        if (request.headers.get("x-orbital-prefix") or "").strip():
            return "/aigf"
        return "/aigf" if _via_site_domain(request) else ""

    @api.get("/", response_class=HTMLResponse)
    def index(request: Request):
        """The branded domain is the shop window; the bare endpoint is the studio."""
        if _via_site_domain(request):
            return gallery_html
        return page

    def _under_aigf(html: str) -> str:
        """Re-point a page's root-absolute API calls at the `/aigf` subfolder.

        Caddy hands `/aigf` and `/aigf/*` to this app unrewritten and strips `/aigf` from
        `/aigf/api/*`, so the prefix only has to be added inside the served markup.
        """
        return html.replace('"/api/', '"/aigf/api/').replace("`/api/", "`/aigf/api/")

    @api.get("/aigf", response_class=HTMLResponse)
    @api.get("/aigf/", response_class=HTMLResponse)
    def aigf_studio():
        """The render studio, behind the branded domain's `/aigf` subfolder."""
        return HTMLResponse(content=_under_aigf(page), headers={"cache-control": "no-cache"})

    @api.get("/aigf/gallery", response_class=HTMLResponse)
    @api.get("/aigf/gallery/", response_class=HTMLResponse)
    def aigf_gallery():
        """The public gallery (local catalog + distilled + peers), linked from `/aigf`.

        Same page as `/gallery`, published under the subfolder so the studio's link stays
        on `dubwarz.vip` instead of sending visitors to the bare modal.run hostname.
        """
        return HTMLResponse(content=_under_aigf(gallery_html),
                            headers={"cache-control": "no-cache"})

    @api.get("/health")
    def health():
        return {"ok": True, "app": APP_NAME, "gpu": GPU}

    @api.get("/api/info")
    def info():
        return service.info.remote()

    # ---------------------------------------------------------------- peer network

    def _cluster(request: Request) -> None:
        """Peers present the shared token when one is configured."""
        if CLUSTER_TOKEN and request.headers.get("x-cluster-token", "") != CLUSTER_TOKEN:
            raise HTTPException(status_code=401, detail="cluster token required")

    @api.get("/api/net/peers")
    def net_peers():
        now = time.time()
        table = orbit_net.merge(dict(peer_table.get("table") or {}), [], now=now)
        table[NODE_ID] = orbit_net.normalise_peer({
            "node": NODE_ID, "url": SELF_URL or "https://unset.invalid",
            "caps": ["image", "video", "lora", "batch"], "gpu": GPU, "load": 0, "seen": now,
        })
        peer_table["table"] = table
        return {
            "self": NODE_ID, "url": SELF_URL, "cluster_auth": bool(CLUSTER_TOKEN),
            "seeds": SEED_PEERS, "count": len(table), "peers": table,
            "best_image": orbit_net.pick(table, "image", NODE_ID, now=now),
        }

    @api.post("/api/net/register")
    def net_register(request: Request, payload: dict = Body(...)):
        _cluster(request)
        table = orbit_net.merge(dict(peer_table.get("table") or {}), [payload])
        peer_table["table"] = table
        return {"ok": True, "count": len(table), "registered": payload.get("node")}

    @api.post("/api/net/gossip")
    def net_gossip(request: Request, payload: dict = Body(...)):
        _cluster(request)
        table = orbit_net.merge(dict(peer_table.get("table") or {}), payload.get("peers") or [])
        peer_table["table"] = table
        return {"self": NODE_ID, "peers": list(table.values())}

    @api.post("/api/net/generate")
    def net_generate(payload: dict = Body(...)):
        """Render on the least-loaded peer that advertises image; fall back to local."""
        table = dict(peer_table.get("table") or {})
        best = orbit_net.pick(table, "image", NODE_ID)
        if best and best.get("url", "").startswith("http"):
            try:
                out = orbit_net.forward(best["url"], "/api/generate", payload, CLUSTER_TOKEN)
                if isinstance(out, dict) and not out.get("error"):
                    out["served_by"] = best["node"]
                    return out
            except Exception as exc:
                print(f"[net] peer {best['node']} failed: {exc}")
        result = service.generate.remote(payload)
        result["served_by"] = NODE_ID
        return result

    # ---------------------------------------------------------------- revenue split

    @api.get("/api/econ/terms")
    def econ_terms():
        return {
            "developer_bps": DEVELOPER_BPS,
            "prices_cents": orbit_econ.PRICES,
            "currency": "usd",
            "stripe_configured": bool(STRIPE_SECRET_KEY and STRIPE_CONNECTED_ACCOUNT),
        }

    @api.post("/api/econ/record")
    def econ_record(request: Request, payload: dict = Body(...)):
        _cluster(request)
        usage = payload.get("usage") or {}
        gross = int(payload.get("gross_cents") or orbit_econ.charge_for(usage))
        split = orbit_econ.split_cents(gross, DEVELOPER_BPS)
        totals = dict(usage_ledger.get("totals") or {})
        for key in ("gross_cents", "developer_cents", "platform_cents"):
            totals[key] = int(totals.get(key, 0)) + split[key]
        totals["events"] = int(totals.get("events", 0)) + 1
        usage_ledger["totals"] = totals
        return {"split": split, "totals": totals}

    @api.get("/api/econ/ledger")
    def econ_ledger():
        return {"totals": usage_ledger.get("totals") or {},
                "developer_bps": DEVELOPER_BPS}

    # ---------------------------------------------------------------- gallery + shop

    @api.get("/gallery", response_class=HTMLResponse)
    def gallery_page():
        # The page embeds behaviour, so it must not be served stale after a deploy.
        return HTMLResponse(content=gallery_html, headers={"cache-control": "no-cache"})

    _peer_catalog_cache: dict = {"at": 0.0, "nodes": []}

    def _peer_catalogs(ttl: float = 60.0) -> list:
        """Every other node's catalog, fetched in parallel and cached briefly.

        Peers come from the peer table plus the `ORBITAL_PEERS` seeds, so a node that has
        never gossiped yet still shows up. A dead peer contributes an error row and no
        characters — one bad node must not empty the gallery.
        """
        now = time.time()
        if _peer_catalog_cache["nodes"] and now - _peer_catalog_cache["at"] < ttl:
            return _peer_catalog_cache["nodes"]

        candidates = [
            (p.get("node"), p.get("url"))
            for p in (peer_table.get("table") or {}).values()
            # The heartbeat registers this node too, usually with a placeholder URL when
            # ORBITAL_SELF_URL is unset — never treat our own row as a peer.
            if isinstance(p, dict) and str(p.get("node") or "") != NODE_ID
        ]
        candidates += [("", url) for url in SEED_PEERS]

        rows, seen = [], set()
        for node, url in candidates:
            url = str(url or "").strip().rstrip("/")
            # `unset.invalid` is the sentinel a node registers when ORBITAL_SELF_URL is
            # empty, so it always means "this node" and never a reachable peer.
            if (not url or url in seen or url == SELF_URL
                    or str(node or "") == NODE_ID or "unset.invalid" in url):
                continue
            seen.add(url)
            rows.append((str(node or url), url))

        def one(row):
            node, url = row
            try:
                body = orbit_net.get_json(url, "/api/gallery?scope=local", CLUSTER_TOKEN, timeout=4.0)
                return {"node": node, "url": url, "ok": True,
                        "items": body.get("characters") or []}
            except Exception as exc:
                return {"node": node, "url": url, "ok": False, "items": [],
                        "error": f"{type(exc).__name__}: {exc}"[:140]}

        if rows:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=6) as pool:
                out = list(pool.map(one, rows))
        else:
            out = []
        _peer_catalog_cache.update({"at": now, "nodes": out})
        return out

    @api.get("/api/gallery")
    def gallery_index(request: Request):
        """The global gallery: this node's catalog plus every peer's, deduped by sku.

        `?scope=local` returns this node only — that is what a peer asks for, so the
        network never recurses into itself.
        """
        scope = (request.query_params.get("scope") or "global").strip().lower()
        local_items = catalog.get("characters") or []
        distilled = orbit_girls.newest(dict(girlfriends.get("items") or {}))
        nodes = [{"node": NODE_ID, "url": SELF_URL or "local", "ok": True,
                  "items": local_items}]
        if scope != "local":
            if distilled:
                nodes.append({"node": NODE_ID, "url": SELF_URL or "local", "ok": True,
                              "items": distilled})
            nodes += _peer_catalogs()
        characters = orbit_net.merge_gallery(nodes)
        # Mark the ones whose stock frame is already rendered, so the page can show it
        # immediately instead of an empty card.
        thumb_dir = os.path.join(W, "gallery", "thumbs")
        index = dict(gallery_thumbs.get("items") or {})
        have = set(index)
        if not have:
            try:
                have = {n[:-4] for n in os.listdir(thumb_dir) if n.endswith(".png")}
            except OSError:
                have = set()
        # Absolute URLs in this payload are resolved by the browser against the page's
        # origin, so on the branded domain they must carry the `/aigf` prefix Caddy needs;
        # a direct modal.run caller still gets plain `/api/...`.
        move = _subfolder(request)
        for card in characters:
            sku = str(card.get("sku") or "")
            culture = orbit_culture.for_sku(sku, str(card.get("tag") or ""))
            if culture:
                card["culture"] = culture
            if not sku or sku not in have:
                continue
            # The version makes a re-render a different URL, so a browser that cached the
            # previous frame cannot keep showing it. Without this the thumbnails are stale
            # for as long as the image cache lives, which is a day.
            at = int((index.get(sku) or {}).get("at") or 0)
            if not at:
                try:
                    at = int(os.stat(os.path.join(thumb_dir, f"{sku}.png")).st_mtime)
                except OSError:
                    at = 0
            card["thumb"] = f"{move}/api/gallery/thumb/{sku}?v={at}"
        return {
            "brand": catalog.get("brand", "DUBWARZ"),
            "currency": catalog.get("currency", "usd"),
            "domain": catalog.get("domain", SITE_DOMAIN),
            "scope": scope,
            "count": len(characters),
            "distilled": len(distilled),
            "groups": orbit_girls.summary(characters),
            "nodes": [{"node": n.get("node"), "url": n.get("url"), "ok": bool(n.get("ok")),
                       "count": len(n.get("items") or []), "error": n.get("error")}
                      for n in nodes],
            "characters": characters,
        }

    @api.get("/api/hud/roster")
    def hud_roster(request: Request):
        """The roster in the shape a Second Life HUD can actually read.

        `/api/gallery` carries prompts, culture blocks and peer entries and runs well past
        the 4096 bytes an `llHTTPRequest` can hold, so an LSL caller silently gets "". This
        is the same catalog minus everything a HUD does not draw: the profile sku (which is
        what names her adapter on the render call), a name, a tag and the thumb path. It
        stays around 1 KB for the current roster, with room for 24 escorts.
        """
        nodes = [{"node": NODE_ID, "url": SELF_URL or "local", "ok": True,
                  "items": catalog.get("characters") or []}]
        distilled = orbit_girls.newest(dict(girlfriends.get("items") or {}))
        if distilled:
            nodes.append({"node": NODE_ID, "url": SELF_URL or "local", "ok": True,
                          "items": distilled})
        move = _subfolder(request)
        rows = []
        for card in orbit_net.merge_gallery(nodes):
            sku = str(card.get("sku") or "")
            if not sku:
                continue
            rows.append({
                "sku": sku,
                "name": str(card.get("name") or sku)[:20],
                "tag": str(card.get("tag") or "")[:20],
                "rating": str(card.get("rating") or "")[:8],
                "thumb": f"{move}/api/gallery/thumb/{sku}",
            })
            if len(rows) >= 24:
                break
        return {"ok": True, "count": len(rows), "escorts": rows}

    def _thumb_jpeg(png: bytes, width: int = 640, quality: int = 85) -> bytes:
        """Card-sized JPEG from a full render. 640 px is the most a card ever shows."""
        import io as _io

        from PIL import Image

        with Image.open(_io.BytesIO(png)) as im:
            im = im.convert("RGB")
            if im.width > width:
                im = im.resize((width, max(1, round(im.height * width / im.width))), Image.LANCZOS)
            buf = _io.BytesIO()
            im.save(buf, format="JPEG", quality=quality, optimize=True)
            return buf.getvalue()

    def _remember_thumb(sku: str, jpeg: bytes) -> None:
        import base64 as _b64

        blobs = dict(gallery_blobs.get("items") or {})
        blobs[sku] = {"b64": _b64.b64encode(jpeg).decode("ascii"), "at": int(time.time())}
        if len(blobs) > 200:
            oldest = sorted(blobs.items(), key=lambda kv: int((kv[1] or {}).get("at") or 0))
            for key, _ in oldest[: len(blobs) - 200]:
                blobs.pop(key, None)
        gallery_blobs["items"] = blobs
        index = dict(gallery_thumbs.get("items") or {})
        index[sku] = {"bytes": len(jpeg), "at": int(time.time())}
        gallery_thumbs["items"] = index

    @api.get("/api/gallery/master/{key}")
    def gallery_master(key: str):
        """One master image for a master group: its near-identical frames compressed to a sheet.

        The styles inside a master group are close, so four cards of the same look read as one
        image. Compositing here keeps a collapsed gallery to one request per master instead of
        a wall of near-duplicate thumbnails.
        """
        meta = next((m for m in orbit_culture.masters() if m["key"] == key), None)
        if meta is None:
            raise HTTPException(status_code=404, detail="unknown master group")
        import base64 as _b64

        from fastapi.responses import Response as RawResponse
        from PIL import Image, ImageOps

        blobs = gallery_blobs.get("items") or {}
        tiles: list[tuple[str, bytes]] = []
        for sku in meta["members"]:
            got = (blobs or {}).get(sku) if isinstance(blobs, dict) else None
            if isinstance(got, dict) and got.get("b64"):
                tiles.append((sku, _b64.b64decode(got["b64"])))
                continue
            for ext in (".jpg", ".png"):
                path = os.path.join(W, "gallery", "thumbs", f"{sku}{ext}")
                if os.path.exists(path):
                    with open(path, "rb") as f:
                        tiles.append((sku, f.read()))
                    break
            if len(tiles) >= 6:
                break
        if not tiles:
            raise HTTPException(status_code=404, detail="no frames rendered in this group yet")

        tile_w, tile_h = 300, 375
        cols = min(3, len(tiles))
        rows = (len(tiles) + cols - 1) // cols
        sheet = Image.new("RGB", (tile_w * cols, tile_h * rows), (18, 7, 16))
        placed = 0
        for sku, raw in tiles:
            try:
                im = Image.open(io.BytesIO(raw)).convert("RGB")
            except Exception as exc:
                print(f"[master] skipped {sku}: {exc}")
                continue
            sheet.paste(ImageOps.fit(im, (tile_w, tile_h), Image.LANCZOS),
                        ((placed % cols) * tile_w, (placed // cols) * tile_h))
            placed += 1
        if not placed:
            raise HTTPException(status_code=404, detail="no usable frames")
        buf = io.BytesIO()
        sheet.save(buf, format="JPEG", quality=84, optimize=True)
        return RawResponse(content=buf.getvalue(), media_type="image/jpeg",
                           headers={"cache-control": "public, max-age=120",
                                    "x-master-tiles": str(placed)})

    @api.get("/api/gallery/thumb/{sku}")
    def gallery_thumb(sku: str):
        """The pregenerated stock frame for a gallery character, or 404 if none yet."""
        import base64 as _b64

        from fastapi.responses import Response as RawResponse

        if not orbit_market.SLUG.match(sku):
            raise HTTPException(status_code=400, detail="bad sku")
        cached = (gallery_blobs.get("items") or {}).get(sku)
        if isinstance(cached, dict) and cached.get("b64"):
            return RawResponse(content=_b64.b64decode(cached["b64"]), media_type="image/jpeg",
                               headers={"cache-control": "public, max-age=86400"})
        for ext, media in ((".jpg", "image/jpeg"), (".png", "image/png")):
            path = os.path.join(W, "gallery", "thumbs", f"{sku}{ext}")
            if not os.path.exists(path):
                try:
                    weights_vol.reload()
                except Exception:
                    pass
            if os.path.exists(path):
                with open(path, "rb") as f:
                    return RawResponse(content=f.read(), media_type=media,
                                       headers={"cache-control": "public, max-age=86400"})
        raise HTTPException(status_code=404, detail="no thumbnail")

    @api.post("/api/gallery/recompress")
    async def gallery_recompress(request: Request):
        """Convert stored full-size PNG frames into card-sized JPEGs, in place, no re-render."""
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        limit = int(payload.get("limit") or 0)
        folder = os.path.join(W, "gallery", "thumbs")
        try:
            weights_vol.reload()
        except Exception:
            pass
        names = sorted(n for n in os.listdir(folder) if n.endswith(".png")) if os.path.isdir(folder) else []
        done = []
        for name in names[: limit or len(names)]:
            sku = name[:-4]
            try:
                with open(os.path.join(folder, name), "rb") as f:
                    png = f.read()
                jpeg = _thumb_jpeg(png)
                with open(os.path.join(folder, f"{sku}.jpg"), "wb") as f:
                    f.write(jpeg)
                _remember_thumb(sku, jpeg)
                done.append({"sku": sku, "from_bytes": len(png), "to_bytes": len(jpeg)})
            except Exception as exc:
                done.append({"sku": sku, "error": f"{type(exc).__name__}: {exc}"[:120]})
        if done:
            weights_vol.commit()
        return {"ok": True, "processed": len(done), "remaining": max(0, len(names) - len(done)),
                "items": done}

    @api.put("/api/gallery/thumb/{sku}")
    async def gallery_thumb_put(sku: str, request: Request):
        """Store a rendered frame as a character's stock thumbnail."""
        _guard(request)
        if not orbit_market.SLUG.match(sku):
            raise HTTPException(status_code=400, detail="bad sku")
        body = await request.body()
        if not body or len(body) < 1024 or not body.startswith(b"\x89PNG"):
            return JSONResponse(status_code=400, content={"error": "body must be a PNG over 1 KB"})
        if len(body) > 16_000_000:
            return JSONResponse(status_code=413, content={"error": "PNG larger than 16 MB"})
        # Keep the full frame for durability, but serve the small copy: a 1 MB PNG off the
        # volume takes tens of seconds in this deployment, which is unusable in a card.
        folder = os.path.join(W, "gallery", "thumbs")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, f"{sku}.png"), "wb") as f:
            f.write(body)
        jpeg = _thumb_jpeg(body)
        with open(os.path.join(folder, f"{sku}.jpg"), "wb") as f:
            f.write(jpeg)
        weights_vol.commit()
        _remember_thumb(sku, jpeg)
        return {"ok": True, "sku": sku, "bytes": len(jpeg), "source_bytes": len(body),
                "path": f"gallery/thumbs/{sku}.jpg",
                "url": f"/api/gallery/thumb/{sku}"}

    @api.get("/api/culture")
    def culture_index():
        """One commander, her eight careers, the factions and the dynamics that recruit."""
        return orbit_culture.command()

    @api.get("/api/girlfriends")
    def girlfriend_list():
        """Distilled characters, newest first, with the same classification summary."""
        items = orbit_girls.newest(dict(girlfriends.get("items") or {}))
        return {"count": len(items), "groups": orbit_girls.summary(items), "characters": items}

    @api.post("/api/girlfriend/distill")
    def girlfriend_distill(payload: dict = Body(...)):
        """Classify a prompt and publish it to the global gallery as an AI girlfriend."""
        prompt = str(payload.get("prompt") or "")
        try:
            item = orbit_girls.distill(prompt)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})
        item["node"] = NODE_ID
        item["source"] = SELF_URL or "local"
        store = orbit_girls.add(dict(girlfriends.get("items") or {}), item)
        girlfriends["items"] = store
        return {"ok": True, "item": item, "count": len(store),
                "groups": orbit_girls.summary(orbit_girls.newest(store))}

    # ---------------------------------------------------------------- chat + memory

    def _partner(sku: str):
        """A chat partner: shipped catalog first, then distilled. Nothing else is chattable."""
        for card in catalog.get("characters") or []:
            if card.get("sku") == sku:
                return card
        for card in orbit_girls.newest(dict(girlfriends.get("items") or {}), limit=600):
            if card.get("sku") == sku:
                return card
        return None

    @api.get("/api/chat/{sku}")
    def chat_history(sku: str, request: Request):
        """What she remembers of this visitor: the transcript and the stored facts."""
        try:
            uid = orbit_chat.user_id(request.query_params.get("user_id"))
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})
        memory = orbit_chat.row(chats.get(orbit_chat.key(sku, uid)), sku, uid)
        return {"sku": sku, "user": uid,
                "profile": str(chats.get(orbit_chat.profile_key(uid)) or ""),
                "messages": memory["messages"], "facts": memory["facts"]}

    @api.post("/api/chat/{sku}")
    def chat_send(sku: str, payload: dict = Body(...)):
        """One turn with one character, remembered per visitor across visits.

        Female-only by policy (`orbit_chat.eligible`). The transcript and the facts she has
        been told live in a Dict keyed by (character, visitor), so a returning visitor is
        recognised instead of reintroduced.
        """
        try:
            uid = orbit_chat.user_id(payload.get("user_id"))
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})
        card = _partner(sku)
        if card is None:
            return JSONResponse(status_code=404, content={"error": f"unknown character {sku!r}"})
        if not orbit_chat.eligible(card):
            return JSONResponse(status_code=403, content={"error": "chat is female-only"})
        text = " ".join(str(payload.get("message") or "").split())[:orbit_chat.MAX_MESSAGE]
        if not text:
            return JSONResponse(status_code=400, content={"error": "empty message"})

        memory = orbit_chat.row(chats.get(orbit_chat.key(sku, uid)), sku, uid)
        note = str(chats.get(orbit_chat.profile_key(uid)) or "")
        said = service.talk.remote({
            "system": orbit_chat.persona(card, note, memory["facts"]),
            "messages": orbit_chat.turns(memory, uid) + [{"role": "user", "text": text}],
            "max_new_tokens": 280,
        })
        if said.get("error"):
            return JSONResponse(status_code=503, content={"error": str(said["error"])})
        reply = str(said.get("text") or "").strip()
        if not reply:
            return JSONResponse(status_code=502, content={"error": "she did not answer"})

        memory = orbit_chat.append(memory, "user", text)
        memory = orbit_chat.append(memory, "assistant", reply)
        for fact in orbit_chat.auto_facts(text):
            memory = orbit_chat.remember(memory, fact)
        chats[orbit_chat.key(sku, uid)] = dict(memory)
        return {"reply": reply, "turns": len(memory["messages"]), "facts": memory["facts"]}

    @api.post("/api/profile/{uid}")
    def profile_set(uid: str, payload: dict = Body(...)):
        """Read a visitor's profile picture or avatar once, for every character to see."""
        try:
            user = orbit_chat.user_id(uid)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})
        image = str(payload.get("image") or "").strip()
        if not image:
            return JSONResponse(status_code=400, content={"error": "image is required"})
        seen = service.talk.remote({
            "image": image,
            "temperature": 0.3,
            "max_new_tokens": 220,
            "vision_prompt": (
                "Describe the person or avatar in this picture for someone who will talk "
                "to them: apparent age range, hair, face, build, clothes, mood, setting and "
                "any distinctive detail worth noticing. Two or three sentences."
            ),
        })
        if seen.get("error"):
            return JSONResponse(status_code=503, content={"error": str(seen["error"])})
        note = orbit_chat.profile_note(seen.get("text"))
        if not note:
            return JSONResponse(status_code=502, content={"error": "could not read that picture"})
        chats[orbit_chat.profile_key(user)] = note
        return {"user": user, "profile": note}

    # ---------------------------------------------------------------- escort upsell

    @api.get("/api/studio/config")
    def studio_config_get():
        """The house adapters every render wears, and the names an admin may choose from."""
        return {"loras": _studio_config().get("loras") or [],
                "known": {k: v.get("label") for k, v in CUSTOM_LORAS.items()}}

    @api.post("/api/studio/config")
    def studio_config_set(request: Request, payload: dict = Body(...)):
        """Admin only: replace the house adapters. No deploy, no page, no client control."""
        _guard(request)
        import json

        items = payload.get("loras")
        if not isinstance(items, list) or len(items) > 6:
            return JSONResponse(status_code=400, content={
                "error": "loras must be a list of at most 6 entries"})
        rows: list[dict] = []
        for item in items:
            name = str((item or {}).get("name") if isinstance(item, dict) else item or "").strip()
            if name not in CUSTOM_LORAS:
                return JSONResponse(status_code=400, content={"error": f"unknown LoRA {name!r}"})
            raw = item.get("scale") if isinstance(item, dict) else None
            try:
                value = CUSTOM_LORAS[name].get("default_scale") if raw is None else float(raw)
            except (TypeError, ValueError):
                value = CUSTOM_LORAS[name].get("default_scale")
            rows.append({"name": name,
                         "scale": _clamp(float(value if value is not None else 1.0), 0.0, 2.0)})
        with weights_vol.open(STUDIO_CONFIG_NAME, "w") as fh:
            json.dump({"loras": rows}, fh, indent=2)
        weights_vol.commit()
        _studio_cfg_cache.update({"at": 0.0, "value": None})
        return {"ok": True, "loras": rows}

    @api.get("/api/upsell/{sku}")
    def upsell_record(sku: str):
        """Her personal-gallery record plus the AIGF / AIGF Pro pitch her page shows."""
        rec = upsell.get(sku)
        if isinstance(rec, dict):
            return rec
        card = _partner(sku)
        if card is None:
            return JSONResponse(status_code=404, content={"error": f"unknown character {sku!r}"})
        return orbit_upsell.entry(card, "", [])

    @api.post("/api/upsell/{sku}")
    def upsell_render(sku: str, request: Request, payload: dict = Body(...)):
        """Build her upgraded gallery: VLM recognition, then 1K thumb + 2K zoom (+4K).

        Guarded like every other write. The 1K frame becomes her stock thumbnail, so her
        roster card starts showing the upgraded render; the 2K/4K frames go back to the
        caller, which owns the page they are published on.
        """
        _guard(request)
        card = _partner(sku)
        if card is None:
            return JSONResponse(status_code=404, content={"error": f"unknown character {sku!r}"})
        if not orbit_chat.eligible(card):
            return JSONResponse(status_code=403, content={"error": "the roster is female-only"})
        try:
            sources = orbit_upsell.validate_sources(
                payload.get("images") or payload.get("image"))
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})

        got = service.upsell_gallery.remote({
            "sources": sources,
            # The sku IS the render identity: `upsell_gallery` reads `req["sku"]` and passes
            # it to `generate` as `character`, which is what loads her adapter. Leaving it
            # out silently rendered every upsell through the house pair only.
            "sku": sku,
            "scene": str(payload.get("scene") or ""),
            "seed": payload.get("seed"),
            "strength": payload.get("strength"),
            "download_4k": bool(payload.get("download_4k")),
        })
        if got.get("error"):
            return JSONResponse(status_code=503, content={"error": str(got["error"])})
        renders = got.get("renders") or {}
        if not renders.get("thumb", {}).get("data"):
            return JSONResponse(status_code=502, content={"error": "no render came back"})

        import base64 as _b64
        try:
            jpeg = _thumb_jpeg(_b64.b64decode(renders["thumb"]["data"].split(",", 1)[-1]))
            folder = os.path.join(W, "gallery", "thumbs")
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, f"{sku}.jpg"), "wb") as f:
                f.write(jpeg)
            weights_vol.commit()
            _remember_thumb(sku, jpeg)
        except Exception as exc:
            print(f"[upsell] thumb not stored for {sku}: {exc}")

        record = orbit_upsell.entry(
            card,
            str(got.get("description") or ""),
            [{"size": r.get("size"), "seed": r.get("seed"), "seconds": r.get("seconds")}
             for r in renders.values() if isinstance(r, dict)],
        )
        upsell[sku] = record
        return {"ok": True, "record": record,
                "renders": {k: v.get("data") for k, v in renders.items()},
                "adapters": got.get("adapters") or [],
                "prompt": got.get("prompt"), "seconds": got.get("seconds")}

    @api.post("/api/shop/checkout")
    def shop_checkout(payload: dict = Body(...)):
        """Destination charge: the developer's connected account is paid, the platform keeps a fee."""
        sku = str(payload.get("sku") or "").strip()
        item = next((c for c in (catalog.get("characters") or []) if c.get("sku") == sku), None)
        if item is None:
            return JSONResponse(status_code=404, content={"error": f"unknown sku {sku!r}"})
        cents = int(item.get("price_cents") or 0)
        if not (STRIPE_SECRET_KEY and STRIPE_CONNECTED_ACCOUNT):
            return JSONResponse(status_code=503, content={
                "error": "payments not configured",
                "sku": sku,
                "amount_cents": cents,
                "how": "export STRIPE_SECRET_KEY and STRIPE_CONNECTED_ACCOUNT, then redeploy",
            })
        import base64 as _b64
        import urllib.parse as _up
        import urllib.request as _ur

        params = orbit_econ.stripe_params(cents, DEVELOPER_BPS, STRIPE_CONNECTED_ACCOUNT)
        auth = _b64.b64encode((STRIPE_SECRET_KEY + ":").encode()).decode()
        req = _ur.Request(
            "https://api.stripe.com/v1/payment_intents",
            data=_up.urlencode(params).encode(),
            headers={"Authorization": f"Basic {auth}",
                     "content-type": "application/x-www-form-urlencoded"},
        )
        try:
            with _ur.urlopen(req, timeout=30) as resp:
                intent = _json.loads(resp.read().decode())
        except Exception as exc:
            return JSONResponse(status_code=502, content={"error": f"{type(exc).__name__}: {exc}"[:300]})
        return {
            "client_secret": intent.get("client_secret"),
            "amount_cents": cents,
            "split": orbit_econ.split_cents(cents, DEVELOPER_BPS),
            "sku": sku,
        }

    # ---------------------------------------------------------------- velvet HUD rail
    # The five endpoints the two client scripts speak, and the page an order is delivered
    # on. They were missing while the scripts shipped, which is why an L$100 test order
    # produced nothing anywhere: the HUD posted to /api/commissions, no such route existed,
    # and the L$ had already left the client's account through `llGiveMoney`.

    def _claim_ok(request: Request) -> bool:
        """The wallet key the HUD signs with; empty on the server means open, as before."""
        return not CLAIM_KEY or request.headers.get("x-claim-key", "").strip() == CLAIM_KEY

    def _rail_roster() -> list[dict]:
        """Every character a client may commission: shipped catalog, then distilled."""
        cards = list(catalog.get("characters") or [])
        cards += orbit_girls.newest(dict(girlfriends.get("items") or {}))
        out: list[dict] = []
        seen: set[str] = set()
        for card in cards:
            sku = str(card.get("sku") or "").strip()
            if not sku or sku in seen:
                continue
            seen.add(sku)
            pid = sku[3:] if sku.startswith("dw-") else sku
            out.append({"id": pid, "sku": sku,
                        "name": str(card.get("name") or pid)[:20],
                        "uuid": str(card.get("uuid") or "").strip()})
            if len(out) >= 24:
                break
        return out

    @api.get("/api/roster")
    def rail_roster(request: Request):
        """Who may be commissioned and what each kind costs, in L$ and in USD.

        The HUD derives its button labels from `pricing`, so this table is the single
        source of a price; `POST /api/commissions` prices the same kind from the same
        function and never trusts a number sent by the client.
        """
        move = _subfolder(request)
        rows = []
        for row in _rail_roster():
            card = _partner(row["sku"])
            if card is None or not orbit_chat.eligible(card):
                continue
            rows.append({"id": row["id"], "name": row["name"], "uuid": row["uuid"],
                         "thumb": f"{move}/api/gallery/thumb/{row['sku']}"})
        return {"ok": True, "count": len(rows), "performers": rows,
                "pricing": {"imageUSD": COMMISSION_USD["image"],
                            "testUSD": COMMISSION_USD["test"],
                            "videoUSD": COMMISSION_USD["video"],
                            "lindenRate": LINDEN_RATE,
                            "imageL": linden_price("image"),
                            "testL": linden_price("test"),
                            "videoL": linden_price("video")}}

    @api.get("/api/wallet")
    def wallet_get(request: Request):
        """The balance the HUD draws before it offers anything for sale."""
        client = (request.query_params.get("client") or "").strip()
        name = (request.query_params.get("name") or "").strip()
        if not client:
            return JSONResponse(status_code=400, content={"error": "client is required"})
        # Register the (key, name) pair on every read. A key lives in a script; a name is what
        # an operator can actually read from a chat log. Without this, a client who paid L$
        # to an older HUD has no wallet row at all, so there is nothing to credit and no way
        # to find out who they are — which is exactly how the L$100 went missing.
        row = _wallet_add(client, 0, "", name) if name else _wallet_row(client)
        return {"ok": True, "client": client,
                "balanceL": int(row.get("balanceL") or 0),
                "spentL": int(row.get("spentL") or 0),
                "resident": row.get("resident") or "",
                "updated": row.get("updated") or 0}

    @api.post("/api/wallet/topup")
    def wallet_topup(request: Request, payload: dict = Body(...)):
        """Book a top-up the HUD has already paid into the treasury.

        `llGiveMoney` happens first and is not reversible, so an unbookable top-up loses the
        client's L$. This credits first and answers with the new balance, and a repeated
        POST is the same call — which is why the HUD can safely retry it.
        """
        if not _claim_ok(request):
            return JSONResponse(status_code=401, content={"error": "bad claim key"})
        client = str(payload.get("clientUuid") or "").strip()
        resident = str(payload.get("clientResident") or "").strip()
        try:
            amount = int(payload.get("amountL") or 0)
        except (TypeError, ValueError):
            amount = 0
        if not client:
            return JSONResponse(status_code=400, content={"error": "clientUuid is required"})
        if amount < 1 or amount > 20000:
            return JSONResponse(status_code=400, content={"error": "amountL must be 1..20000"})
        row = _wallet_add(client, amount, "topup", resident)
        print(f"[wallet] +{amount} L$ {client} -> {row['balanceL']}")
        return {"ok": True, "client": client, "creditedL": amount,
                "balanceL": int(row.get("balanceL") or 0)}

    @api.post("/api/wallet/credit")
    def wallet_credit(request: Request, payload: dict = Body(...)):
        """Studio reconcile: credit L$ a client paid before the rail could book it.

        Both testers parted with real L$ while `/api/wallet/topup` was a 404, so the money
        sits in the treasury with no balance behind it. This is the one-off repair path;
        it is guarded by the same claim key the HUD uses.
        """
        if not _claim_ok(request):
            return JSONResponse(status_code=401, content={"error": "bad claim key"})
        client = str(payload.get("clientUuid") or "").strip()
        resident = str(payload.get("clientResident") or "").strip()
        # A client can arrive here with only the name that shows in their HUD log — the UUID
        # is in their script, not in the transcript an operator reads. Accepting the name for
        # an existing wallet is what makes "he lost his L$100" fixable from a chat paste.
        if not client and resident:
            client, _row = _wallet_by_name(resident)
            client = client or ""
        try:
            amount = int(payload.get("amountL") or 0)
        except (TypeError, ValueError):
            amount = 0
        if not client or amount < 1 or amount > 20000:
            return JSONResponse(status_code=400, content={
                "error": "clientUuid (or a clientResident that already has a wallet) and "
                         "amountL (1..20000) are required",
                "hint": "GET /api/wallet/find?name=<resident> lists wallets by name"})
        row = _wallet_add(client, amount, str(payload.get("note") or "studio credit"),
                          str(payload.get("clientResident") or ""))
        return {"ok": True, "client": client, "creditedL": amount,
                "balanceL": int(row.get("balanceL") or 0)}

    @api.get("/api/wallet/refusals")
    def wallet_refusals(request: Request):
        """Every order this rail refused for a short wallet — the paid-but-undelivered list.

        A stale HUD debits L$ in-world before it posts, so these are the clients who lost
        money; each row carries the name and key needed to credit them back.
        """
        if not _claim_ok(request):
            return JSONResponse(status_code=401, content={"error": "bad claim key"})
        rows = []
        for key in refusals.keys():
            row = refusals.get(key)
            if isinstance(row, dict):
                rows.append(dict(row))
        rows.sort(key=lambda r: int(r.get("lastAt") or 0), reverse=True)
        return {"ok": True, "count": len(rows), "refusals": rows[:50],
                "note": "credit a row with POST /api/wallet/credit "
                        "{\"clientUuid\": ..., \"amountL\": row.needL}"}

    @api.get("/api/wallet/find")
    def wallet_find(request: Request):
        """Reconcile by resident name: which wallet is this, and what is in it?"""
        if not _claim_ok(request):
            return JSONResponse(status_code=401, content={"error": "bad claim key"})
        want = " ".join((request.query_params.get("name") or "").split()).lower()
        rows = []
        for key in wallets.keys():
            row = wallets.get(key)
            if not isinstance(row, dict):
                continue
            if want and " ".join(str(row.get("resident") or "").split()).lower() != want:
                continue
            rows.append({"clientUuid": key, "resident": row.get("resident") or "",
                         "balanceL": int(row.get("balanceL") or 0),
                         "spentL": int(row.get("spentL") or 0),
                         "updated": int(row.get("updated") or 0),
                         "last": row.get("last") or ""})
        rows.sort(key=lambda r: r["updated"], reverse=True)
        return {"ok": True, "count": len(rows), "wallets": rows[:20]}

    def _public_base(request: Request) -> str:
        """Absolute origin for links handed to a HUD: scheme + host + the /aigf prefix.

        A HUD opens exactly the string it is given. `/v/<id>` is only correct behind Caddy and
        `/aigf/v/<id>` is only correct on modal.run directly, so the server resolves it once,
        here, from the request it is answering instead of leaving the script to guess.
        """
        sub = _subfolder(request)
        host = (request.headers.get("x-forwarded-host") or request.headers.get("host")
                or SITE_DOMAIN)
        host = host.split(",")[0].strip()
        scheme = (request.headers.get("x-forwarded-proto") or "https").split(",")[0].strip()
        # Modal's edge rewrites Host, and Caddy only sometimes forwards the visitor's. A link
        # the client clicks must be on the branded domain whenever the request came through
        # it — Caddy's own prefix header is the reliable signal, the forwarded host the
        # fallback. A direct modal.run caller keeps modal's hostname, which also works.
        forwarded = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip().lower()
        if sub and SITE_DOMAIN and (request.headers.get("x-orbital-prefix")
                                    or forwarded.endswith(SITE_DOMAIN)):
            return f"{scheme}://{SITE_DOMAIN}{sub}"
        return f"{scheme}://{host}{sub}"

    @api.post("/api/commissions")
    def commission_create(request: Request, payload: dict = Body(...)):
        """Debit the wallet, record the order, and start the render in the background.

        Answers in well under a second so the HUD's `llHTTPRequest` gets its `orderId`
        inside LSL's ~30 s window; generation is spawned, and the client polls the order.
        """
        import uuid as _uuid

        kind = str(payload.get("kind") or "image").strip().lower()
        if kind not in COMMISSION_USD:
            return JSONResponse(status_code=400, content={"error": f"unknown kind {kind!r}"})
        if kind == "video":
            return JSONResponse(status_code=503, content={
                "error": "video commissions are not enabled yet — nothing was charged",
                "kind": kind})
        client = str(payload.get("clientUuid") or "").strip()
        if not client:
            return JSONResponse(status_code=400, content={"error": "clientUuid is required"})

        sku = str(payload.get("character") or "").strip()
        if not sku:
            pid = str(payload.get("performer") or "").strip().lower()
            sku = next((r["sku"] for r in _rail_roster() if r["id"].lower() == pid), "")
        card = _partner(sku) if sku else None
        if card is None:
            return JSONResponse(status_code=404, content={"error": f"unknown character {sku!r}"})
        if not orbit_chat.eligible(card):
            return JSONResponse(status_code=403, content={"error": "the roster is female-only"})

        price = linden_price(kind)
        balance = int(_wallet_row(client).get("balanceL") or 0)
        resident = str(payload.get("clientResident") or "").strip()[:64]
        # Register the pair even on a refusal: this is the only moment a stale HUD's client
        # tells us who they are, and it is exactly the moment the studio needs to credit them.
        if resident:
            _wallet_add(client, 0, "", resident)
        if balance < price:
            prev = refusals.get(client)
            prev = dict(prev) if isinstance(prev, dict) else {"count": 0}
            prev.update({"clientUuid": client, "resident": resident or prev.get("resident") or "",
                         "kind": kind, "priceL": price, "needL": price,
                         "balanceL": balance, "count": int(prev.get("count") or 0) + 1,
                         "lastAt": int(time.time())})
            refusals[client] = prev
            print(f"[refused] {client} ({resident or 'no name'}) wanted {kind} {price} L$ "
                  f"with {balance} L$ · attempt {prev['count']}")
            return JSONResponse(status_code=402, content={
                "error": "wallet balance is short — nothing was charged by this order",
                "balanceL": balance, "needL": price,
                "hint": "top up first; if an older HUD already debited L$ in-world, the "
                        "studio can credit it from this refusal record"})

        note = " ".join(str(payload.get("note") or "").split())[:240]
        prompt = str(card.get("prompt") or "").strip()
        if note:
            prompt = (prompt + ", " + note) if prompt else note
        # The finish is part of the product, not the client's wording: a prompt that only says
        # "club floor under neon" renders soft and airbrushed, which is what a paying gallery
        # frame must not be.
        prompt = (prompt + ", " + COMMISSION_QUALITY) if prompt else COMMISSION_QUALITY
        order_id = _uuid.uuid4().hex[:12]
        rec = {
            "orderId": order_id,
            "status": "pending",
            "kind": kind,
            "sku": sku,
            "performer": str(card.get("name") or "")[:40],
            "performerId": str(payload.get("performer") or "")[:40],
            "escortUuid": str(card.get("uuid") or "")[:36],
            "clientUuid": client[:36],
            "clientResident": str(payload.get("clientResident") or "")[:64],
            "priceL": price,
            "note": note,
            "prompt": prompt[:1200],
            "private": kind in PRIVATE_KINDS,
            # The worker fills in the run's real steps/adapters; these are placeholders so the
            # receipt always has the field rather than a missing key.
            "steps": 0, "upscale": 0, "adapters": [],
            "seed": int(time.time()) % 100000,
            "via": str(payload.get("via") or "hud")[:16],
            "createdAt": int(time.time()),
            # Absolute from the start: this is the string the HUD prints in chat and the
            # client clicks, and a base-relative path is wrong on one of the two hosts.
            "openUrl": _public_base(request) + "/v/" + order_id,
        }
        commissions[order_id] = rec
        row = _wallet_add(client, -price, "order " + order_id)
        commission_render.spawn(order_id)      # the render is minutes; the HTTP answer is not
        print(f"[order] {order_id} {kind} {sku} {price} L$ for {client} -> {row['balanceL']} left")
        return {"ok": True, "orderId": order_id, "status": "pending", "kind": kind,
                "priceL": price, "balanceL": int(row.get("balanceL") or 0),
                "openUrl": rec["openUrl"],
                "url": rec["openUrl"], "page": rec["openUrl"],
                "imageUrl": _public_base(request) + "/api/commission/" + order_id + "/image"}

    @api.get("/api/commissions")
    def commission_list(request: Request):
        """Recent orders — the studio's view of what the rail actually took."""
        client = (request.query_params.get("client") or "").strip()
        rows = [dict(v) for v in (commissions.get(_k) for _k in commissions.keys())
                if isinstance(v, dict)]
        if client:
            rows = [r for r in rows if str(r.get("clientUuid") or "") == client]
        rows.sort(key=lambda r: int(r.get("createdAt") or 0), reverse=True)
        rows = rows[: int(request.query_params.get("limit") or 20)]
        return {"ok": True, "count": len(rows),
                "orders": [{k: r.get(k) for k in
                            ("orderId", "status", "kind", "sku", "performer", "clientResident",
                             "priceL", "createdAt", "seconds", "steps", "upscale", "adapters",
                             "private", "error", "openUrl")}
                           for r in rows],
                "pricing": {"testL": linden_price("test"), "imageL": linden_price("image"),
                            "videoL": linden_price("video"), "lindenRate": LINDEN_RATE}}

    @api.get("/api/commission/{order_id}")
    def commission_status(order_id: str, request: Request):
        """What the HUD polls: pending, ready (open it), or failed (already refunded)."""
        order_id = _order_key(order_id) or order_id
        rec = commissions.get(order_id)
        if not isinstance(rec, dict):
            return JSONResponse(status_code=404, content={"error": "unknown order"})
        client = str(rec.get("clientUuid") or "")
        return {"ok": True, "orderId": order_id,
                "status": str(rec.get("status") or "pending"),
                "kind": rec.get("kind"), "priceL": int(rec.get("priceL") or 0),
                "performer": rec.get("performer"), "seconds": rec.get("seconds"),
                "bytes": rec.get("bytes"), "error": rec.get("error") or "",
                # The render's own report: which adapters actually loaded (her LoRA plus the
                # house pair) and the schedule the worker ran, so a receipt can be checked
                # instead of trusted.
                "steps": int(rec.get("steps") or 0),
                "upscale": int(rec.get("upscale") or 0),
                "width": int(rec.get("width") or 0), "height": int(rec.get("height") or 0),
                "adapters": rec.get("adapters") or [],
                "balanceL": int(_wallet_row(client).get("balanceL") or 0) if client else 0,
                # Always canonical and absolute, never the stored string: orders created
                # before this change carry a relative `/aigf/v/<id>`, and a HUD that prefixes
                # its base would turn that into `/aigf/aigf/v/<id>` — a dead link on a paid
                # order. Rebuilding here makes old and new records identical to the client.
                "openUrl": _public_base(request) + "/v/" + order_id,
                # Compatibility aliases: HUD copies in the wild read different field names,
                # and a client who is already paid must see their link under whichever one
                # their build knows instead of falling through to a dead end.
                "url": _public_base(request) + "/v/" + order_id,
                "page": _public_base(request) + "/v/" + order_id,
                "imageUrl": _public_base(request) + "/api/commission/" + order_id + "/image"}

    @api.get("/api/commission/{order_id}/image")
    def commission_image(order_id: str):
        """The delivered frame itself: JPEG for the page, PNG if the master is all we have."""
        from fastapi.responses import Response as RawResponse

        order_id = _order_key(order_id) or order_id
        rec = commissions.get(order_id)
        if not isinstance(rec, dict):
            raise HTTPException(status_code=404, detail="unknown order")
        folder = os.path.join(W, COMMISSION_DIR)
        # A private (test) frame is reachable by whoever holds the link — that is how Second
        # Life opens it — but it is never indexable, so a test proof cannot surface in a
        # search result or an archive beside the public gallery.
        private = bool(rec.get("private")) or str(rec.get("kind")) in PRIVATE_KINDS
        headers = {"cache-control": "private, max-age=86400" if private
                   else "public, max-age=86400"}
        if private:
            headers["x-robots-tag"] = "noindex, nofollow, noarchive"
        for name, media in ((order_id + ".jpg", "image/jpeg"),
                            (order_id + ".png", "image/png")):
            path = os.path.join(folder, name)
            if not os.path.exists(path):
                try:
                    weights_vol.reload()
                except Exception:
                    pass
            if os.path.exists(path):
                with open(path, "rb") as f:
                    return RawResponse(content=f.read(), media_type=media, headers=headers)
        detail = "that order has no frame yet" if rec.get("status") != "failed" \
            else "that order failed and was refunded"
        raise HTTPException(status_code=404, detail=detail)

    def _order_page(request: Request, rec: dict) -> str:
        """The view page a finished order opens: the frame, its receipt, and the galleries."""
        import html as _html

        esc = _html.escape
        order_id = str(rec.get("orderId") or "")
        status = str(rec.get("status") or "pending")
        kind = str(rec.get("kind") or "image")
        word = {"test": "test picture", "video": "video"}.get(kind, "picture")
        private = bool(rec.get("private")) or kind in PRIVATE_KINDS
        # A test frame is a private proof, so the PAGE says so to crawlers and to the
        # reader: no index, no archive, and no claim that it is gallery work.
        robots = '<meta name="robots" content="noindex,nofollow,noarchive">' if private else ""
        badge = ("<div class='box' style='border-color:#6b3b5d;color:#ffb7ec'>"
                 "PRIVATE TEST FRAME — this is your proof of the look. It is not in any "
                 "gallery, any roster card or any public page, and it never will be. Only "
                 "this link shows it.</div>") if private else ""
        base = _subfolder(request) + "/api/commission/" + order_id
        # `/aigf/gallery` is served by this app on every host, so it is the one gallery URL
        # that is correct both through Caddy and on the bare modal.run endpoint.
        gallery, studio = "/aigf/gallery", "/aigf"
        # A pending order reloads ITSELF rather than a computed URL: the page is already
        # registered at both /v/<id> and /aigf/v/<id>, and a relative reload keeps whichever
        # one the viewer is on.
        refresh = "" if status in ("ready", "failed") else \
            '<meta http-equiv="refresh" content="6">'
        if status == "ready":
            shown = (f'<a href="{esc(base)}/image" target="_blank" rel="noopener">'
                     f'<img src="{esc(base)}/image" alt="Your commissioned {esc(word)}"></a>')
            line = (f"Delivered — L${int(rec.get('priceL') or 0)} was taken from your wallet when "
                    f"the order was placed.")
        elif status == "failed":
            shown = ("<div class='box'>This generation failed. The L$"
                     f"{int(rec.get('priceL') or 0)} has been refunded to your wallet "
                     "automatically — nothing is owed.</div>")
            line = "Failed — refunded to your wallet."
        else:
            shown = "<div class='box'>Still generating — this page reloads itself. "
            "Your order stands and your place is kept.</div>"
            line = "In progress."
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">{refresh}{robots}
<title>Velvet — order {esc(order_id)}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{{margin:0;background:#0b0910;color:#f4e9f6;font:15px/1.55 system-ui,Segoe UI,sans-serif}}
 .wrap{{max-width:760px;margin:0 auto;padding:28px 20px 64px}}
 h1{{font-size:19px;letter-spacing:.14em;text-transform:uppercase;color:#ff7ad9;margin:0 0 4px}}
 .meta{{color:#b9a8c4;font-size:13px;margin:0 0 18px}}
 img{{width:100%;border-radius:14px;border:1px solid #33243c;display:block}}
 .box{{border:1px solid #33243c;border-radius:12px;padding:16px;background:#150f1c;margin:0 0 18px}}
 .row{{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}}
 a.btn{{display:inline-block;padding:11px 16px;border-radius:10px;text-decoration:none;
  background:#ff7ad9;color:#16060f;font-weight:700;font-size:14px}}
 a.ghost{{background:transparent;color:#ff9de6;border:1px solid #6b3b5d}}
 .kv{{display:grid;grid-template-columns:auto 1fr;gap:6px 16px;font-size:13px;color:#b9a8c4}}
 .kv b{{color:#f4e9f6;font-weight:600}}
</style></head><body><div class="wrap">
<h1>Velvet — order delivered</h1>
<p class="meta">{line}</p>
{badge}
{shown}
<div class="box" style="margin-top:18px"><div class="kv">
<div>Order</div><b>{esc(order_id)}</b>
<div>Kind</div><b>{esc(word)}</b>
<div>Performer</div><b>{esc(str(rec.get('performer') or rec.get('sku') or ''))}</b>
<div>Price</div><b>L${int(rec.get('priceL') or 0)}</b>
<div>Status</div><b>{esc(status)}</b>
<div>Rendered at</div><b>{int(rec.get('steps') or 0)} steps · {int(rec.get('width') or 0)}×{int(rec.get('height') or 0)}{' · private' if private else ''}</b>
<div>Created</div><b>{int(rec.get('createdAt') or 0) and time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(int(rec.get('createdAt') or 0))) or ''}</b>
</div></div>
<p class="meta">This page is your receipt and your copy — keep the link. Every frame here is
made fresh by the studio, not a stock photo.</p>
<div class="row">
<a class="btn" href="{esc(gallery)}">Open the full gallery</a>
<a class="btn ghost" href="{esc(studio)}">Velvet studio</a>
<a class="btn ghost" href="{esc(base)}/image" target="_blank" rel="noopener">Full-size frame</a>
</div>
</div></body></html>"""

    @api.post("/api/vision/judge")
    def vision_judge(request: Request, payload: dict = Body(...)):
        """QA: ask the VLM that already conditions renders to judge a delivered frame.

        The quality bar here is a human judgement, so this asks the model that reads images
        on this rail for a blunt verdict instead of standing up a second vision service. It
        is a diagnostic: it renders nothing, charges nothing and stores nothing.
        """
        _guard(request)
        image = str(payload.get("image") or "").strip()
        if not image:
            return JSONResponse(status_code=400, content={"error": "image is required"})
        question = " ".join(str(payload.get("question") or "").split())[:400] or (
            "Judge this as a gallery-quality portrait for a paying client. Is it sharp, "
            "detailed and well-formed, or soft, low-detail or artifacted? Be blunt in two "
            "sentences, then score it out of 10 for gallery display.")
        seen = service.talk.remote({"image": image, "temperature": 0.2,
                                    "max_new_tokens": 300, "vision_prompt": question})
        if seen.get("error"):
            return JSONResponse(status_code=503, content={"error": str(seen["error"])})
        return {"ok": True, "verdict": str(seen.get("text") or "").strip()}

    # ---------------------------------------------------------------- profit engine

    @api.get("/api/engine")
    def engine_status():
        """Who can take an order, what each host costs, and what 1,000 orders would earn.

        Read-only and unguarded on purpose: it discloses whether a key is present, never the
        key — the same disclosure `/api/lora/token` already makes — and it is the page that
        answers "is this profitable" without a redeploy.
        """
        quotes = {}
        for kind, price in COMMISSION_USD.items():
            tier = COMMISSION_RENDER.get(kind) or COMMISSION_RENDER["test"]
            quotes[kind] = {
                "priceL": linden_price(kind),
                "priceUsd": float(price),
                "providers": [
                    orbit_engine.quote(kind, float(price), pid, int(tier["width"]),
                                       int(tier["height"]), int(tier.get("upscale") or 0))
                    for pid in orbit_engine.RENDER_HOSTS
                ],
            }
        return {**orbit_engine.status(), "quote": quotes,
                "plan1000": orbit_engine.plan("image", float(COMMISSION_USD["image"]), 1000)}

    @api.get("/v/{order_id}", response_class=HTMLResponse)
    @api.get("/aigf/v/{order_id}", response_class=HTMLResponse)
    def commission_page(order_id: str, request: Request):
        """The delivered view page. Registered at both paths so Caddy and modal.run agree."""
        # Strip whatever prefix the client's HUD printed: `comm-d5f0d4df05` and
        # `d5f0d4df05` are the same delivered frame, and a paid client must never be told
        # their order does not exist because of a label.
        order_id = _order_key(order_id) or order_id
        rec = commissions.get(order_id)
        if not isinstance(rec, dict):
            return HTMLResponse(status_code=404,
                                content="<h1>Unknown order</h1><p>That order id is not on "
                                        "this studio. If a HUD gave you this link, copy the "
                                        "whole thing including any prefix and try again.</p>")
        return HTMLResponse(content=_order_page(request, rec),
                            headers={"cache-control": "no-cache"})

    # ---------------------------------------------------------------- marketplace

    @api.get("/api/market/listings")
    def market_index():
        store = dict(market_listings.get("items") or {})
        if market_seed:
            # Merge the baked-in listings on every read: the dict is only seeded once, so a
            # deploy that adds files would otherwise stay invisible until it was cleared.
            merged = orbit_market.merge(store, market_seed)
            if merged != store:
                market_listings["items"] = merged
            store = merged
        items = sorted(store.values(), key=lambda m: str(m.get("slug")))
        return {
            "count": len(items),
            "signed": bool(MARKET_SECRET),
            "listings": items,
            "thumbs": "/api/market/thumb/<slug>",
        }

    @api.get("/api/market/listing/{slug}")
    def market_one(slug: str):
        store = orbit_market.merge(dict(market_listings.get("items") or {}), market_seed)
        item = store.get(slug)
        if not item:
            return JSONResponse(status_code=404, content={"error": f"no listing {slug!r}"})
        return item

    @api.post("/api/market/publish")
    def market_publish(request: Request, payload: dict = Body(...)):
        """Validate, sign (when a market secret exists), and keep the manifest."""
        _cluster(request)
        manifest = dict(payload)
        manifest.setdefault("signed_at", int(time.time()))
        problems = orbit_market.validate(manifest)
        if problems:
            return JSONResponse(status_code=400, content={"error": "invalid listing", "problems": problems})
        # A published manifest replaces the seeded copy, so it has to carry the thumbnail
        # pointer too or the card loses its image until the next seed merge.
        manifest.setdefault("thumb", f"/api/market/thumb/{manifest['slug']}")
        if MARKET_SECRET and not orbit_market.verify(manifest, MARKET_SECRET):
            manifest = orbit_market.sign(manifest, MARKET_SECRET)
        store = orbit_market.merge(dict(market_listings.get("items") or {}), [manifest])
        market_listings["items"] = store
        return {"ok": True, "slug": manifest["slug"], "signature": manifest.get("signature"),
                "count": len(store)}

    @api.post("/api/market/announce")
    def market_announce(request: Request, payload: dict = Body(...)):
        """Push a manifest to every known peer, so listings spread without a central server."""
        _cluster(request)
        manifest = dict(payload)
        manifest.setdefault("signed_at", int(time.time()))
        problems = orbit_market.validate(manifest)
        if problems:
            return JSONResponse(status_code=400, content={"error": "invalid listing", "problems": problems})
        manifest.setdefault("thumb", f"/api/market/thumb/{manifest['slug']}")
        if MARKET_SECRET and not orbit_market.verify(manifest, MARKET_SECRET):
            manifest = orbit_market.sign(manifest, MARKET_SECRET)
        store = orbit_market.merge(dict(market_listings.get("items") or {}), [manifest])
        market_listings["items"] = store
        sent, failed = [], []
        for peer in list((peer_table.get("table") or {}).values()):
            if not isinstance(peer, dict) or peer.get("node") == NODE_ID:
                continue
            try:
                orbit_net.forward(peer["url"], "/api/market/publish", manifest, CLUSTER_TOKEN, timeout=8.0)
                sent.append(peer.get("node"))
            except Exception as exc:
                failed.append({"node": peer.get("node"), "error": str(exc)[:120]})
        return {"ok": True, "slug": manifest["slug"], "sent": sent, "failed": failed}

    @api.get("/api/market/thumb/{slug}")
    def market_thumb(slug: str):
        """Deterministic 640x800 thumbnail, so a listing needs no image asset."""
        from fastapi.responses import Response as RawResponse

        if not orbit_market.SLUG.match(slug):
            raise HTTPException(status_code=400, detail="bad slug")

        # A rendered frame wins over the generated gradient. Another container wrote it, so
        # the volume has to be re-scanned before concluding it is missing.
        thumb_path = os.path.join(W, "market", "thumbs", f"{slug}.png")
        if not os.path.exists(thumb_path):
            try:
                weights_vol.reload()
            except Exception:
                pass
        if os.path.exists(thumb_path):
            with open(thumb_path, "rb") as f:
                return RawResponse(content=f.read(), media_type="image/png",
                                   headers={"cache-control": "public, max-age=3600"})

        from PIL import Image, ImageDraw

        import hashlib as _h

        seed = int(_h.sha256(slug.encode()).hexdigest()[:8], 16)
        img = Image.new("RGB", (640, 800), (7, 7, 10))
        draw = ImageDraw.Draw(img)
        for y in range(0, 800, 8):
            t = y / 800.0
            r = int(10 + t * (24 + seed % 46))
            g = int(8 + t * (10 + (seed >> 8) % 34))
            b = int(26 + t * (30 + (seed >> 16) % 58))
            draw.rectangle([0, y, 640, y + 8], fill=(r, g, b))
        draw.rectangle([28, 640, 612, 641], fill=(226, 84, 44))
        draw.text((28, 660), slug.upper(), fill=(242, 237, 228))
        draw.text((28, 686), "DUBWARZ", fill=(138, 133, 124))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return RawResponse(content=buf.getvalue(), media_type="image/png",
                           headers={"cache-control": "public, max-age=3600"})

    @api.put("/api/market/thumb/{slug}")
    async def market_thumb_put(slug: str, request: Request):
        """Store a rendered frame as the listing's thumbnail."""
        _guard(request)
        if not orbit_market.SLUG.match(slug):
            raise HTTPException(status_code=400, detail="bad slug")
        body = await request.body()
        if not body or len(body) < 1024 or not body.startswith(b"\x89PNG"):
            return JSONResponse(status_code=400, content={"error": "body must be a PNG over 1 KB"})
        if len(body) > 12_000_000:
            return JSONResponse(status_code=413, content={"error": "PNG larger than 12 MB"})
        folder = os.path.join(W, "market", "thumbs")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{slug}.png")
        with open(path, "wb") as f:
            f.write(body)
        weights_vol.commit()
        return {"ok": True, "slug": slug, "bytes": len(body),
                "path": f"market/thumbs/{slug}.png"}

    @api.post("/api/generate")
    def generate(payload: dict = Body(...)):
        try:
            result = service.generate.remote(payload)
        except Exception as exc:  # surface container errors to the UI instead of a 500 page
            return JSONResponse(status_code=500, content={"error": f"{type(exc).__name__}: {exc}"})
        if isinstance(result, dict) and result.get("error"):
            return JSONResponse(status_code=400, content=result)
        return result

    @api.post("/api/video")
    def generate_video(payload: dict = Body(...)):
        try:
            result = video.generate.remote(payload)
        except Exception as exc:
            return JSONResponse(status_code=500, content={"error": f"{type(exc).__name__}: {exc}"})
        if isinstance(result, dict) and result.get("error"):
            return JSONResponse(status_code=400, content=result)
        return result

    @api.get("/api/lora/token")
    def lora_token_status():
        """Whether a key is configured and whether writes need one — never the key itself."""
        return {
            "env_token": bool(os.environ.get("CIVITAI_TOKEN", "").strip()),
            "access_key_required": bool(ACCESS_KEY),
            "accepts": ["X-Access-Key", "Authorization: Bearer"],
        }

    @api.post("/api/lora/token")
    def lora_token_store(request: Request, payload: dict = Body(...)):
        """Store the Civitai token in the volume; empty string clears it."""
        _guard(request)
        try:
            return store_lora_token.remote((payload.get("token") or "").strip())
        except Exception as exc:
            return JSONResponse(status_code=500, content={"error": f"{type(exc).__name__}: {exc}"})

    @api.post("/api/lora/download_all")
    def lora_download_all(request: Request):
        _guard(request)
        try:
            return download_all_loras.remote()
        except Exception as exc:
            return JSONResponse(status_code=500, content={"error": f"{type(exc).__name__}: {exc}"})

    @api.post("/api/lora/download")
    def lora_download(request: Request, payload: dict = Body(...)):
        """Ask the server to fetch a registry LoRA into the volume, then report.

        `url` is passed through for a name that is not in the registry, and for promoting
        an adapter that already sits on the volume (`file:///weights/loras/...`) into the
        stable name a profile renders under.
        """
        _guard(request)
        name = (payload.get("name") or "").strip()
        url = (payload.get("url") or "").strip()
        try:
            return download_custom_lora.remote(name, url, "", bool(payload.get("force")))
        except Exception as exc:
            return JSONResponse(status_code=400, content={"error": f"{type(exc).__name__}: {exc}"})

    @api.get("/api/lora/{job}")
    def lora_status(job: str):
        from modal.functions import FunctionCall

        try:
            res = FunctionCall.from_id(job).get(timeout=0)
        except TimeoutError:
            return {"status": "training"}
        except Exception as exc:
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:300]}
        return {
            "status": "ready",
            "bytes": res["bytes"],
            "notes": res.get("notes", ""),
            "txt": f"/api/lora/{job}/txt",
            "stored": res.get("stored"),
            "rank": res["rank"],
            "steps": res["steps"],
            "loss_first": res["loss_first"],
            "loss_last": res["loss_last"],
            "seconds": res["seconds"],
            "seed": res["seed"],
            "base": res["base"],
            "url": f"/api/lora/{job}/file",
        }

    @api.get("/api/lora/{job}/file")
    def lora_file(job: str):
        from fastapi.responses import Response as RawResponse
        from modal.functions import FunctionCall

        try:
            res = FunctionCall.from_id(job).get(timeout=0)
        except TimeoutError:
            return JSONResponse(status_code=409, content={"error": "still training"})
        except Exception as exc:
            return JSONResponse(status_code=500, content={"error": str(exc)[:300]})
        return RawResponse(
            content=base64.b64decode(res["data"]),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="krea2_seed{res["seed"]}.safetensors"'
            },
        )

    @api.get("/api/lora/{job}/txt")
    def lora_notes(job: str):
        """The sidecar notes for a fitted LoRA, as downloaded plain text."""
        from fastapi.responses import Response as RawResponse
        from modal.functions import FunctionCall

        try:
            res = FunctionCall.from_id(job).get(timeout=0)
        except TimeoutError:
            return JSONResponse(status_code=409, content={"error": "still training"})
        except Exception as exc:
            return JSONResponse(status_code=500, content={"error": str(exc)[:300]})
        return RawResponse(
            content=res.get("notes", "").encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="krea2_seed{res["seed"]}.txt"'
            },
        )

    @api.post("/api/video/stitch")
    def stitch_clips(payload: dict = Body(...)):
        clips = payload.get("clips") or []
        if not isinstance(clips, list) or not clips:
            return JSONResponse(status_code=400, content={"error": "clips[] is required"})
        try:
            result = stitch.remote(clips, int(payload.get("fps") or 24))
        except Exception as exc:
            return JSONResponse(status_code=500, content={"error": f"{type(exc).__name__}: {exc}"})
        if isinstance(result, dict) and result.get("error"):
            return JSONResponse(status_code=400, content=result)
        return result

    @api.get("/api/video/info")
    def video_info():
        try:
            return video.info.remote()
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    return api


# --------------------------------------------------------------------------------------
# Local entrypoint: prepare weights, warm the worker, smoke-test one image
# --------------------------------------------------------------------------------------


@app.local_entrypoint()
def main(
    prompt: str = "a red fox sitting in fresh snow, golden hour, photorealistic",
    download_first: bool = False,
    video_download: str = "",
    lora_download: str = "",
    lora_url: str = "",
    lora_token: str = "",
    lora_all: bool = False,
    variant: str = "uncensored",
    width: int = 1024,
    height: int = 1024,
    steps: int = 8,
    out: str = "out",
):
    import pathlib

    if download_first:
        print(download.remote(variant="all"))
    if video_download:
        print(download_video.remote(video_download if isinstance(video_download, str) else "all"))
    if lora_token:
        print(store_lora_token.remote(lora_token))
    if lora_all:
        print(download_all_loras.remote())
    if lora_download:
        print(download_custom_lora.remote(lora_download, lora_url))

    t0 = time.time()
    res = Krea2().generate.remote(
        {
            "prompt": prompt,
            "variant": variant,
            "width": width,
            "height": height,
            "steps": steps,
            "num_images": 1,
        }
    )
    if res.get("error"):
        raise SystemExit(f"generation failed: {res['error']}")
    pathlib.Path(out).mkdir(parents=True, exist_ok=True)
    for i, im in enumerate(res["images"]):
        path = pathlib.Path(out) / f"krea2_{im['seed']}_{i}.png"
        path.write_bytes(base64.b64decode(im["data"]))
        print(f"wrote {path} ({im['width']}x{im['height']}, {im['seconds']}s)")
    print(f"total {time.time() - t0:.1f}s · variant={res['variant']} · encoder={res['text_encoder']}")
