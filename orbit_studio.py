"""Which adapter renders a given profile, and the studio link that profile carries.

A character is a profile sku (`dw-sophia`, `gf-juno-04812`) or a display name (`Sophia`).
The studio resolves it to one LoRA when that character owns one, and to no LoRA when she
does not — a near-miss is worse than nothing, because it would render someone else's face.

Pure Python: importable and testable without a GPU or a deploy.
"""

from __future__ import annotations

import re


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")


def _tokens(*parts) -> set[str]:
    """Words long enough to identify a person; sku prefixes like `dw` are ignored."""
    out: set[str] = set()
    for part in parts:
        for tok in re.split(r"[^a-z0-9]+", str(part or "").lower()):
            if len(tok) >= 4:
                out.add(tok)
    return out


def href(sku: str) -> str:
    """The generator link a profile shows, with her already loaded."""
    key = slug(sku)
    return f"/aigf?c={key}" if key else "/aigf"


def pick(catalog: dict, character: str) -> str:
    """The catalog key belonging to this character, or "" when she has no adapter.

    `catalog` is `/api/loras`'s shape: `{key: {label, trigger, available, default_scale}}`.
    An exact sku/name match beats a shared word, so `kessa` cannot be satisfied by a LoRA
    that merely mentions "dock pilot".
    """
    want = _tokens(character)
    if not want or not isinstance(catalog, dict):
        return ""
    scored: list[tuple[int, bool, str]] = []
    for key, spec in catalog.items():
        if not isinstance(spec, dict) or spec.get("available") is False:
            continue
        shared = want & _tokens(key, spec.get("label", ""), spec.get("trigger", ""))
        if not shared:
            continue
        scored.append((len(shared), slug(key) == slug(character) or slug(key) == slug(character).removeprefix("dw-"), key))
    if not scored:
        return ""
    scored.sort(key=lambda row: (row[1], row[0], row[2]))
    return scored[-1][2]


def scale(catalog: dict, key: str, fallback: float = 1.0) -> float:
    spec = (catalog or {}).get(key) or {}
    try:
        return float(spec.get("default_scale") if spec.get("default_scale") is not None else fallback)
    except (TypeError, ValueError):
        return fallback


def stack(catalog: dict, character: str) -> list[dict]:
    """The stack the generator sends for a profile: hers and nothing else."""
    key = pick(catalog, character)
    return [{"name": key, "scale": scale(catalog, key)}] if key else []


def blend(catalog: dict, character: str, house: list | None = None) -> list[dict]:
    """Her adapter first, then the admin's house adapters, each name once.

    `house` is `studio.json`'s list — dicts `{"name": ..., "scale": ...}` or bare names —
    so the body/style pair most renders should wear is configuration, not code. Hers keeps
    position zero: identity wins over a house look, and a house entry never displaces her.
    """
    rows = stack(catalog, character)
    seen = {row["name"] for row in rows}
    for item in house or []:
        name = str((item or {}).get("name") if isinstance(item, dict) else item or "").strip()
        if not name or name in seen or name not in catalog:
            continue
        raw = item.get("scale") if isinstance(item, dict) else None
        try:
            value = scale(catalog, name) if raw is None else float(raw)
        except (TypeError, ValueError):
            value = scale(catalog, name)
        seen.add(name)
        rows.append({"name": name, "scale": max(0.0, min(2.0, value))})
    return rows


def profile_block(sku: str, name: str = "") -> dict:
    """The generator card an escort profile shows: one click, her adapter already chosen."""
    who = str(name or "her")
    return {
        "tier": "AIGF Pro",
        "line": f"{who}'s generator: her adapter and look are chosen for her, so the render is her.",
        "cta": f"Render {who}",
        "href": href(sku),
    }
