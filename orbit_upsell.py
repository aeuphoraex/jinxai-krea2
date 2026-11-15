"""An escort's personal AIGF gallery: 1K thumb, 2K zoom, 4K download, and the pitch.

Pure Python — no model call, no network — so the rules can be checked without a GPU. The
rendering itself lives in `app.py` (VLM recognition + img2img + optional video), and the
shape produced here is what an escort's `/a/<name>` page renders.

The size ladder is deliberate and is the whole product:

  * 1024 px  — the thumbnail in her roster card and personal gallery (cheap, everywhere)
  * 2048 px  — the zoom view on the page (the paid-looking one)
  * 3840 px  — direct download only, never inlined on the page
"""

from __future__ import annotations

import re
import time

import orbit_studio

SIZES = {"thumb": 1024, "zoom": 2048, "download": 3840}
MIN_SOURCES = 1
MAX_SOURCES = 3
MAX_NOTE = 700


def validate_sources(images) -> list[str]:
    """One to three source images of the performer, newest first. Raises ValueError."""
    if isinstance(images, str):
        images = [images]
    kept = [str(i or "").strip() for i in (images or []) if str(i or "").strip()]
    if not kept:
        raise ValueError("at least one source image is required")
    if len(kept) > MAX_SOURCES:
        raise ValueError(f"at most {MAX_SOURCES} source images (got {len(kept)})")
    return kept


def identity_prompt(description: str, scene: str = "") -> str:
    """The img2img prompt that keeps her recognisable while upgrading the frame.

    The VLM's reading of the source is folded in verbatim so the render keeps the face,
    hair and wardrobe that make her *her*; only light, film and framing are asked to change.
    """
    note = " ".join(str(description or "").split())[:MAX_NOTE]
    body = (
        "jinx_film, the same woman as the reference image — identical face, hair, skin tone "
        "and wardrobe; upgrade only the light, lens and finish: cinematic key light, shallow "
        "depth of field, 35mm film grain, crisp detail in the eyes and fabric"
    )
    if note:
        body += f". She is: {note}"
    if scene:
        body += f". Scene: {' '.join(str(scene).split())[:200]}"
    return body


def tiers(width: int = SIZES["zoom"]) -> dict:
    """Pixel sizes actually produced, so the page never claims a size it did not render."""
    return {"thumb": SIZES["thumb"], "zoom": width, "download": SIZES["download"]}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")


def plan_copy(name: str, character: dict | None = None) -> dict:
    """The upsell block an escort page shows: free gallery, AIGF, then AIGF Pro.

    Copy only — no prices are invented here. The numbers belong to the plan itself, which
    is billed through Velvet, so this stays true if pricing changes.
    """
    who = str((character or {}).get("name") or name or "her")
    return {
        "headline": f"More of {who}, rendered by her",
        "free": {
            "tier": "AIGF",
            "line": f"Her public gallery — the 1K frames, free to browse, one render on the house.",
            "cta": "Open her AIGF gallery",
            "href": "/aigf/gallery",
        },
        "pro": {
            "tier": "AIGF Pro",
            "line": (
                "Her deck: 2K zoom renders of the same face, 4K originals to download, and a "
                "clip where the frame she is standing in starts moving."
            ),
            "cta": "Unlock AIGF Pro",
            "href": "/aigf#plans",
        },
        # One click from her page into the generator, with her adapter already selected: the
        # entry point that turns a profile view into a render.
        "studio": orbit_studio.profile_block(
            str((character or {}).get("sku") or ""), who),
    }


def entry(character: dict, description: str, renders: list, video: str = "") -> dict:
    """One character's personal-gallery record, as stored in the Dict."""
    sku = str(character.get("sku") or "")
    if not sku:
        raise ValueError("character has no sku")
    now = int(time.time())
    return {
        "sku": sku,
        "name": str(character.get("name") or sku),
        "page": f"/a/{slug(character.get('name') or sku)}",
        "description": " ".join(str(description or "").split())[:MAX_NOTE],
        "renders": [r for r in renders or [] if isinstance(r, dict)][:3],
        "video": str(video or ""),
        "sizes": tiers(),
        "plans": plan_copy(str(character.get("name") or sku), character),
        "updated_at": now,
    }


def store_put(store: dict | None, record: dict, limit: int = 500) -> dict:
    """Insert newest-first and keep the Dict bounded."""
    store = dict(store or {})
    store[record["sku"]] = record
    if len(store) > limit:
        oldest = sorted(store.values(), key=lambda r: int(r.get("updated_at") or 0))
        for drop in oldest[: len(store) - limit]:
            store.pop(drop.get("sku"), None)
    return store
