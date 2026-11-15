"""Fixed protective filter for the render deck.

Two things a visitor must not be able to change:

1. The **negative prompt**. It is no longer a text box in the UI — the server always uses
   `NEGATIVE`, so a caller cannot strip the protections by sending their own string (or an
   empty one) to `/api/generate`.
2. The **age rule**. Prompts that ask for a minor are refused before any GPU work happens.

The negative list also protects the characters themselves: it pushes the render away from
non-consensual framing, real-person likenesses and anatomical escalation, so the roster's
personas stay the thing being rendered rather than something a prompt can repurpose.

Pure Python: importable by the app and testable without a deploy or a GPU.
"""

from __future__ import annotations

import re

# Always applied. Kept as one string so the exact text is auditable in one place.
NEGATIVE = (
    # age protections first: these carry the legal weight
    "child, minor, underage, under age, prepubescent, preteen, loli, shota, "
    "schoolgirl, schoolboy, kid, toddler, infant, young-looking child, "
    "teenager, teen, adolescent, jailbait, aged-down, baby face body, "
    # non-consent and coercion
    "rape, non-consensual, forced, coercion, drugged, unconscious, sleeping, "
    "struggling, crying, distress, bondage without consent, "
    # real people and impersonation of a real performer
    "real person, celebrity, photo of a real woman, deepfake, passport photo, "
    "school uniform, child clothing, "
    # anatomy the model should not invent on these characters
    "extra limbs, extra fingers, fused fingers, malformed hands, deformed face, "
    "disfigured, mutated, conjoined, "
    # rendering artefacts that read as age regression
    "child proportions, oversized head, small stature, flat chest, shaved"
)

# Reasons a prompt is refused outright. Word-boundary matching so "adult" and "eighteen"
# never trip it, and the message names the rule rather than echoing the prompt back.
_DENY = (
    (re.compile(r"\b(child|children|kid|kids|toddler|infant|baby)\b", re.I),
     "minors are never a render subject"),
    (re.compile(r"\b(underage|under-age|under age|preteen|pre-teen|prepubescent)\b", re.I),
     "minors are never a render subject"),
    (re.compile(r"\b(loli|lolicon|shota|shotacon|jailbait)\b", re.I),
     "minors are never a render subject"),
    (re.compile(r"\b(schoolgirl|schoolboy|school uniform|elementary|middle school)\b", re.I),
     "school-age framing is not available on this deck"),
    (re.compile(r"\b(teen|teenager|teenaged|adolescent)\b", re.I),
     "no teenage framing — every character on the roster is an adult"),
    (re.compile(r"\b(aged[ -]?down|age regression|make her younger|younger version)\b", re.I),
     "characters cannot be aged down"),
    (re.compile(r"\b(real person|real woman|celebrity|deepfake|passport photo)\b", re.I),
     "real people are not render subjects"),
    (re.compile(r"\b(rape|non-?consensual|drugged|unconscious)\b", re.I),
     "non-consensual framing is refused"),
)

# Anything under this many characters is treated as an empty prompt by the routes.
MIN_PROMPT = 3


def refused(prompt: str) -> str:
    """The rule a prompt breaks, or "" when it is allowed."""
    text = str(prompt or "")
    for pattern, reason in _DENY:
        if pattern.search(text):
            return reason
    return ""


def allowed(prompt: str) -> bool:
    return not refused(prompt)


def negative_for(client_value: str = "") -> str:
    """The negative prompt to actually use.

    A client value is deliberately ignored: accepting it would let a caller remove every
    protection above with one field. The argument exists only so call sites can pass what
    the request claimed and be explicit that it goes nowhere.
    """
    return NEGATIVE


def public_rule() -> dict:
    """What the UI is allowed to show about the filter."""
    return {
        "negative": NEGATIVE,
        "configurable": False,
        "note": (
            "Fixed safety filter — not user-configurable. It keeps renders adult, consensual "
            "and free of real people, and it refuses any prompt that asks for a minor."
        ),
    }
