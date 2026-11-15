"""Signed listings for a peer-to-peer adapter marketplace.

A listing is a small JSON manifest with a price, a thumbnail reference, and a hash of the
weights it sells. There is no central catalogue: every node stores the manifests it has seen
and can hand them to a peer. `merge` keeps the newest signed_at per slug, so replication is
last-write-wins and idempotent.

Integrity is a shared-secret HMAC over canonical JSON plus a sha256 of the weights. That is
enough to stop accidental corruption and casual tampering between cooperating nodes; it is
not a public-key system and does not stop a node that holds the secret.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
REQUIRED = ("slug", "name", "price_cents", "seller")


def canonical(manifest: dict) -> bytes:
    body = {k: v for k, v in manifest.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign(manifest: dict, secret: str) -> dict:
    out = dict(manifest)
    out["signature"] = hmac.new(secret.encode(), canonical(manifest), hashlib.sha256).hexdigest()
    return out


def verify(manifest: dict, secret: str) -> bool:
    if not secret:
        return False
    given = str(manifest.get("signature") or "")
    want = hmac.new(secret.encode(), canonical(manifest), hashlib.sha256).hexdigest()
    return bool(given) and hmac.compare_digest(given, want)


def validate(manifest: dict) -> list[str]:
    """Everything wrong with a manifest, as short strings an API can return."""
    problems: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest is not an object"]
    for field in REQUIRED:
        if field not in manifest:
            problems.append(f"missing {field}")
    if "slug" in manifest and not SLUG.match(str(manifest.get("slug"))):
        problems.append("slug must be 2-41 chars of a-z, 0-9, hyphen")
    try:
        price = int(manifest.get("price_cents"))
        if price < 0:
            problems.append("price_cents must be >= 0")
        if price > 100_000:
            problems.append("price_cents above $1000 — check the units")
    except (TypeError, ValueError):
        problems.append("price_cents must be an integer of cents")
    name = str(manifest.get("name") or "")
    if not name.strip():
        problems.append("name is empty")
    if len(name) > 80:
        problems.append("name longer than 80 chars")
    sha = manifest.get("weights_sha256")
    if sha and (len(str(sha)) != 64 or any(c not in "0123456789abcdef" for c in str(sha))):
        problems.append("weights_sha256 must be 64 lowercase hex chars")
    return problems


def merge(local: dict, incoming: list, limit: int = 2000) -> dict:
    """Union by slug; the newest signed_at wins. Malformed entries are dropped, not raised."""
    out = {k: v for k, v in (local or {}).items() if isinstance(v, dict)}
    for manifest in incoming or []:
        if validate(manifest):
            continue
        slug = str(manifest["slug"])
        current = out.get(slug)
        if current and int(current.get("signed_at") or 0) > int(manifest.get("signed_at") or 0):
            continue
        out[slug] = manifest
    if len(out) > limit:
        newest = sorted(out.items(), key=lambda kv: int(kv[1].get("signed_at") or 0), reverse=True)
        out = dict(newest[:limit])
    return out


def digest_file(path: str) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()
