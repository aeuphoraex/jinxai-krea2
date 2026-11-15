"""Character chat: persona, per-user memory and the profile note a VLM writes.

Pure Python and offline — no model call happens here, so it is cheap to unit-check. The
model work (the unfiltered Qwen3-VL) lives in `app.py`; this module only decides who may be
chatted with, what the character is told about the user, and how much of the transcript
survives the next turn.

Chat is female-only by policy: a character is eligible unless its manifest says otherwise,
because every shipped and distilled character in this catalog is a woman.
"""

from __future__ import annotations

import re
import time

USER_ID = re.compile(r"^[A-Za-z0-9_-]{3,64}$")
MAX_MESSAGE = 1200
MAX_TURNS = 24          # messages kept in the transcript (12 exchanges)
MAX_FACTS = 40          # remembered lines about one user, per character
MAX_PROFILE = 900       # characters of the VLM's appearance note


def user_id(raw) -> str:
    """Normalise a visitor id, or raise ValueError. Nothing else is trusted as an id."""
    uid = str(raw or "").strip()
    if not USER_ID.match(uid):
        raise ValueError("user_id must be 3-64 characters of [A-Za-z0-9_-]")
    return uid


def eligible(character: dict) -> bool:
    """Female-only: the catalog has no male entries, so this guards future ones."""
    if not isinstance(character, dict):
        return False
    gender = str(character.get("gender") or "female").strip().lower()
    return gender in ("female", "f", "woman", "she")


def key(sku: str, uid: str) -> str:
    return f"{sku}:{uid}"


def profile_key(uid: str) -> str:
    return f"profile:{uid}"


def row(raw, sku: str, uid: str) -> dict:
    """One user's memory of one character: transcript plus what she has been told."""
    got = raw if isinstance(raw, dict) else {}
    if not got:
        return {"sku": sku, "user": uid, "messages": [], "facts": [], "updated_at": 0}
    return {
        "sku": sku,
        "user": uid,
        "messages": [m for m in got.get("messages") or [] if isinstance(m, dict)][-MAX_TURNS:],
        "facts": [str(f)[:200] for f in got.get("facts") or []][-MAX_FACTS:],
        "updated_at": int(got.get("updated_at") or 0),
    }


def append(memory: dict, who: str, text: str, now: float | None = None) -> dict:
    """Add one turn and trim to the tail, so a Dict value cannot grow without bound."""
    memory = dict(memory)
    msgs = list(memory.get("messages") or [])
    msgs.append({"role": who, "text": str(text)[:MAX_MESSAGE]})
    memory["messages"] = msgs[-MAX_TURNS:]
    memory["updated_at"] = int(now if now is not None else time.time())
    return memory


def remember(memory: dict, fact: str) -> dict:
    """Store one durable line about the user, newest last, duplicates folded."""
    memory = dict(memory)
    fact = " ".join(str(fact or "").split())[:200]
    if not fact:
        return memory
    facts = [f for f in memory.get("facts") or [] if f.lower() != fact.lower()]
    facts.append(fact)
    memory["facts"] = facts[-MAX_FACTS:]
    return memory


def profile_note(vlm_text: str) -> str:
    """The VLM's reading of a profile picture, reduced to one storable note."""
    text = " ".join(str(vlm_text or "").split())
    return text[:MAX_PROFILE]


def persona(character: dict, notes: str = "", facts: list | None = None) -> str:
    """The system prompt: who she is, what she knows about this visitor, and the rules."""
    name = str(character.get("name") or "her")
    tag = str(character.get("tag") or "crew")
    group = str(character.get("group") or "general")
    culture = character.get("culture") or {}
    lines = [
        f"You are {name}, a character on the Velvet orbit roster — {group}, {tag}.",
        str(character.get("tagline") or "").strip(),
    ]
    if culture:
        if culture.get("career"):
            lines.append(f"Career: {culture['career']}. Duty: {culture.get('duty') or ''}.")
        if culture.get("phase"):
            lines.append(f"Where she is in her story: {culture['phase']}.")
        faction = culture.get("faction") or {}
        if faction.get("name"):
            lines.append(f"Faction: {faction['name']} — {faction.get('doctrine') or ''}.")
        if culture.get("pop") or culture.get("music") or culture.get("art"):
            lines.append(
                f"Taste: {culture.get('pop') or ''} / {culture.get('music') or ''} / "
                f"{culture.get('art') or ''}."
            )
    lines += [
        "",
        "You are talking privately with one visitor. Stay in character: first person, warm,",
        "specific, no assistant voice, no bullet lists, no mention of models, prompts or policy.",
        "You are an adult and so is he; speak freely, be flirtatious when he is, but never",
        "describe real people and never claim to be human. Keep replies to 2-4 sentences unless",
        "he asks for more, and ask about him — he is the reason the conversation exists.",
    ]
    if notes:
        lines.append("")
        lines.append("What you can see of him (his profile picture / avatar):")
        lines.append(notes)
        lines.append("Use it naturally — notice a detail, compliment something specific — but do")
        lines.append("not recite it back as a list, and do not describe it unprompted twice.")
    if facts:
        lines.append("")
        lines.append("What you already know about him from earlier conversations:")
        lines += [f"- {f}" for f in facts]
    return "\n".join(line for line in lines if line is not None).strip()


AUTO = (
    re.compile(r"\b(?:my name is|i'm|i am|call me)\s+([A-Z][\w'\-]{1,24})", re.I),
    re.compile(r"\bi (?:live in|am from|come from)\s+([\w' \-]{2,40})", re.I),
    re.compile(r"\bi (?:love|like|enjoy)\s+([\w' \-]{2,40})", re.I),
)


def auto_facts(text: str) -> list[str]:
    """Facts a visitor stated outright, so a later visit still knows his name.

    Only explicit self-description is captured — no model call, no guessing — which keeps
    the cost of remembering at zero and the stored facts auditable.
    """
    out = []
    for pattern in AUTO:
        for hit in pattern.findall(str(text or ""))[:3]:
            fact = " ".join(str(hit).split()).strip(" .,;:!?")
            if 1 < len(fact) <= 60:
                out.append(fact)
    return out


def turns(memory: dict, uid: str, limit: int = MAX_TURNS) -> list[dict]:
    """The transcript in the shape the model wants, oldest first."""
    out = []
    for msg in (memory.get("messages") or [])[-limit:]:
        role = "assistant" if str(msg.get("role")) == "assistant" else "user"
        text = str(msg.get("text") or "").strip()
        if text:
            out.append({"role": role, "text": text})
    return out
