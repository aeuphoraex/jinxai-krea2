"""Distil a free-text prompt into a gallery character ("AI girlfriend").

Offline and deterministic: no model call, no network. The prompt is classified into one of
the shipped catalog's archetypes, named from that archetype's pool by prompt hash, and
rewritten into the gallery's house style. The manifest has the catalog's exact field set plus
`group`, `kind` and `created_at`, so a distilled character lists and renders like a shipped one.

Nothing here is a person: the output is a prompt, a seed, an aspect and a classification.
"""

from __future__ import annotations

import hashlib
import re
import time

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
MAX_PROMPT = 280

# Same tags the shipped catalog uses, plus the group each one belongs to. First match wins.
CLASSES = (
    {"tag": "pilot", "group": "flight deck",
     "words": ("pilot", "cockpit", "flight", "shuttle", "aviator", "fighter")},
    {"tag": "navigator", "group": "flight deck",
     "words": ("navigat", "star chart", "route", "compass", "helm", "astrometr")},
    {"tag": "officer", "group": "command",
     "words": ("officer", "captain", "command", "signals", "bridge", "console")},
    {"tag": "dockmaster", "group": "dock",
     "words": ("dock", "hangar", "cargo", "crane", "landing bay", "freight")},
    {"tag": "engineer", "group": "machine shop",
     "words": ("engineer", "mechanic", "reactor", "thruster", "wrench", "machinist")},
    {"tag": "medic", "group": "infirmary",
     "words": ("medic", "doctor", "nurse", "clinic", "surgeon", "medbay")},
    {"tag": "courier", "group": "transit",
     "words": ("courier", "runner", "messenger", "delivery", "dispatch", "bike")},
    {"tag": "scavenger", "group": "derelict",
     "words": ("scaveng", "salvage", "wreck", "derelict", "scrap", "junkyard")},
    {"tag": "dancer", "group": "night deck",
     "words": ("danc", "burlesque", "club", "stage", "performer", "cabaret")},
    {"tag": "aristocrat", "group": "upper ring",
     "words": ("aristocrat", "heiress", "noble", "gala", "duchess", "silk")},
    {"tag": "crew", "group": "general", "words": ()},
)

# Shipped catalog entries carry a tag but no group; this is the same mapping used for distilled
# ones, so the gallery's classification summary speaks one vocabulary.
TAG_GROUP = {cls["tag"]: cls["group"] for cls in CLASSES}

NAMES = {
    "pilot": ("Kessa", "Iris", "Wren", "Aster", "Rook", "Halo"),
    "navigator": ("Nyra", "Vela", "Ptolemy", "Sable", "Lyra", "Cinder"),
    "officer": ("Tamsin", "Maren", "Voss", "Halden", "Sera", "Quill"),
    "dockmaster": ("Orla", "Bracken", "Duna", "Moss", "Kade", "Hollow"),
    "engineer": ("Mira", "Tess", "Rivet", "Anvil", "Juno", "Flint"),
    "medic": ("Vesper", "Ada", "Noor", "Solace", "Ember", "Quiet"),
    "courier": ("Sable", "Pace", "Kite", "Dash", "Nell", "Swift"),
    "scavenger": ("Rook", "Tin", "Ash", "Marrow", "Grist", "Scrap"),
    "dancer": ("Vesper", "Lux", "Calla", "Nyx", "Rue", "Sway"),
    "aristocrat": ("Iselle", "Margot", "Celeste", "Aurelia", "Odile", "Vienne"),
    "crew": ("Mira", "Nell", "Juno", "Wren", "Tess", "Calla"),
}


def _seed(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)


def classify(text: str) -> dict:
    """Which archetype a prompt belongs to. Falls back to `crew`, never raises."""
    low = text.lower()
    for cls in CLASSES:
        if any(word in low for word in cls["words"]):
            return cls
    return CLASSES[-1]


def _trim(text: str, limit: int) -> str:
    text = text.strip(" ,.;:-")
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].strip(" ,.;:-")
    return cut or text[:limit]


def distill(prompt: str, now: float | None = None) -> dict:
    """Turn one prompt into a gallery manifest. Raises ValueError on an unusable prompt."""
    raw = " ".join(str(prompt or "").split())
    if len(raw) < 3:
        raise ValueError("prompt must be at least 3 characters")
    if len(raw) > MAX_PROMPT:
        raw = raw[:MAX_PROMPT].rstrip()

    low = raw.lower()
    cls = classify(low)
    seed = _seed(low)

    names = NAMES.get(cls["tag"]) or NAMES["crew"]
    name = names[seed % len(names)]
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    sku = f"gf-{slug}-{seed % 100000:05d}"
    if not SLUG.match(sku):
        raise ValueError("could not build a sku from that prompt")

    styled = raw if low.startswith("jinx_film") else f"jinx_film, {raw}"
    return {
        "sku": sku,
        "name": name,
        "tagline": _trim(f"{cls['group']} · {raw}", 78),
        "prompt": styled,
        "seed": seed % (2 ** 31),
        "aspect": "4:5",
        "price_cents": 0,
        "tag": cls["tag"],
        "group": cls["group"],
        "rating": "sfw",
        "adapter": None,
        "kind": "distilled",
        "created_at": int(now if now is not None else time.time()),
    }


def add(store: dict, item: dict, limit: int = 600) -> dict:
    """Insert newest-first and drop the oldest beyond `limit` so the Dict cannot grow forever."""
    store = dict(store or {})
    store[item["sku"]] = item
    if len(store) > limit:
        oldest = sorted(store.values(), key=lambda m: int(m.get("created_at") or 0))
        for drop in oldest[: len(store) - limit]:
            store.pop(drop["sku"], None)
    return store


def newest(store: dict, limit: int = 120) -> list:
    rows = [m for m in (store or {}).values() if isinstance(m, dict)]
    rows.sort(key=lambda m: int(m.get("created_at") or 0), reverse=True)
    return rows[:limit]


def summary(items: list) -> list:
    """Group counts for the page: `[{group, count, tags: {tag: n}}]`, biggest group first."""
    groups: dict = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        tag = str(item.get("tag") or "crew")
        group = str(item.get("group") or TAG_GROUP.get(tag, "general"))
        row = groups.setdefault(group, {"group": group, "count": 0, "tags": {}})
        row["count"] += 1
        row["tags"][tag] = row["tags"].get(tag, 0) + 1
    return sorted(groups.values(), key=lambda r: (-r["count"], r["group"]))
