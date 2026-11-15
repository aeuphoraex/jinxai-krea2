"""Peer-table logic for ORBITAL nodes.

Pure Python on purpose: no Modal import, so it can be unit-tested locally and reused by
any node that speaks the same JSON. The network is deliberately dumb — a table of nodes,
a heartbeat timestamp, and a least-loaded pick. No NAT traversal, no relays, no crypto
beyond a shared cluster token.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

CAPS = ("image", "video", "lora", "batch")


def normalise_peer(raw: dict) -> dict | None:
    """Coerce an untrusted registration into the one shape the table stores."""
    if not isinstance(raw, dict):
        return None
    url = str(raw.get("url") or "").strip().rstrip("/")
    if not url.startswith("http://") and not url.startswith("https://"):
        return None
    node = str(raw.get("node") or urllib.parse.urlsplit(url).netloc).strip()[:80]
    if not node:
        return None
    caps = [c for c in (raw.get("caps") or []) if c in CAPS]
    try:
        load = max(0, int(raw.get("load") or 0))
    except (TypeError, ValueError):
        load = 0
    try:
        seen = float(raw.get("seen") or time.time())
    except (TypeError, ValueError):
        seen = time.time()
    return {
        "node": node,
        "url": url[:300],
        "caps": caps or ["image"],
        "load": load,
        "gpu": str(raw.get("gpu") or "")[:24],
        "seen": seen,
    }


def merge(table: dict, incoming: list, now: float | None = None, max_age: float = 900.0) -> dict:
    """Fold registrations into the table, dropping entries we have not heard from."""
    now = now or time.time()
    out = {
        k: v
        for k, v in (table or {}).items()
        if isinstance(v, dict) and now - float(v.get("seen", 0)) <= max_age
    }
    for raw in incoming or []:
        peer = normalise_peer(raw)
        if not peer:
            continue
        current = out.get(peer["node"])
        if current and float(current.get("seen", 0)) > peer["seen"]:
            continue
        out[peer["node"]] = peer
    return out


def pick(table: dict, kind: str = "image", self_node: str = "", now: float | None = None,
         max_age: float = 900.0) -> dict | None:
    """Least-loaded live peer that advertises the capability. Never returns self."""
    now = now or time.time()
    best = None
    best_key = None
    for peer in (table or {}).values():
        if not isinstance(peer, dict) or peer.get("node") == self_node:
            continue
        if kind not in (peer.get("caps") or []):
            continue
        if now - float(peer.get("seen", 0)) > max_age:
            continue
        key = (int(peer.get("load") or 0), str(peer.get("node")))
        if best is None or key < best_key:
            best, best_key = peer, key
    return best


def get_json(url: str, path: str, token: str = "", timeout: float = 5.0) -> dict:
    """GET a peer's JSON. Raises on transport or JSON failure, so callers can mark the node down."""
    req = urllib.request.Request(url.rstrip("/") + path, headers={"accept": "application/json"})
    if token:
        req.add_header("x-cluster-token", token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def merge_gallery(nodes: list, limit: int = 400) -> list:
    """Fold per-node catalogs into one gallery, first node winning on a duplicate sku.

    Each node is `{"node": str, "url": str, "items": [...]}`. The local node goes first,
    so a character this server owns is never replaced by a peer's copy of the same sku.
    """
    seen: set[str] = set()
    items: list = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        for raw in node.get("items") or []:
            if not isinstance(raw, dict):
                continue
            sku = str(raw.get("sku") or "").strip()
            if not sku or sku in seen:
                continue
            seen.add(sku)
            item = dict(raw)
            item["node"] = node.get("node") or ""
            item["source"] = node.get("url") or ""
            items.append(item)
            if len(items) >= limit:
                return items
    return items


def forward(url: str, path: str, payload: dict, token: str = "", timeout: float = 300.0) -> dict:
    """POST a job to a peer. Raises on transport or JSON failure — callers fall back."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + path,
        data=body,
        headers={"content-type": "application/json", "accept": "application/json"},
    )
    if token:
        req.add_header("x-cluster-token", token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))
