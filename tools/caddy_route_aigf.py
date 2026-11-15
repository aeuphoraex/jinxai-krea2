"""Serve the ORBITAL (krea2) backend at dubwarz.vip/aigf/ instead of at the index.

Rename here is about placement, not plumbing: the branded domain's root keeps serving the
main index (the local server on 127.0.0.1:8792), and only the `/aigf` subfolder is sent to
the Modal endpoint:

  * `/aigf/api/*`  → strip the `/aigf` prefix, then proxy to Modal (the studio's fetch calls)
  * `/aigf`, `/aigf/` → proxy to Modal, where the app serves the studio page
  * everything else → 127.0.0.1:8792, byte-for-byte as before

Host is pinned to the Modal hostname because Modal routes on Host. The change is applied to
the running Caddy through its admin API (no restart, no elevation) and written into
`tools/caddy/run_config.json` so a restart from that file keeps the same shape.

    python tools/caddy_route_aigf.py --dry-run
    python tools/caddy_route_aigf.py --apply
"""

from __future__ import annotations

import argparse
import copy
import json
import pathlib
import urllib.request

ADMIN = "http://127.0.0.1:2019"
MODAL_HOST = "xzyorb--orbital.us-east.modal.run"
DOMAIN = "dubwarz.vip"
LOCAL_INDEX = "127.0.0.1:8792"

RUN_CONFIG = pathlib.Path(__file__).resolve().parent / "caddy" / "run_config.json"

_PROXY_MODAL = {
    "handler": "reverse_proxy",
    "flush_interval": -1,
    "upstreams": [{"dial": MODAL_HOST + ":443"}],
    "transport": {"protocol": "http", "tls": {}},
    # `X-Orbital-Prefix` is the app's signal that this request arrived through the
    # subfolder: it answers with the prefixed URLs the page needs (e.g. thumbnails),
    # without having to guess from host headers the edge may rewrite.
    "headers": {"request": {"set": {"Host": [MODAL_HOST],
                                    "X-Orbital-Prefix": ["/aigf"]}}},
}

_PROXY_INDEX = {
    "handler": "reverse_proxy",
    "flush_interval": -1,
    "upstreams": [{"dial": LOCAL_INDEX}],
}

# Order matters: the API route must match before the catch-all page route.
AIGF_ROUTES = [
    {
        "match": [{"path": ["/aigf/api/*"]}],
        "terminal": True,
        "handle": [
            {"handler": "rewrite", "strip_path_prefix": "/aigf"},
            _PROXY_MODAL,
        ],
    },
    {
        "match": [{"path": ["/aigf", "/aigf/", "/aigf/*"]}],
        "terminal": True,
        "handle": [_PROXY_MODAL],
    },
    {"handle": [_PROXY_INDEX]},
]


def hosts_of(route: dict) -> list[str]:
    return [str(h) for m in route.get("match") or [] for h in m.get("host") or []]


def _flatten_handlers(handle: list) -> list:
    """Collect the handlers worth keeping from a route's handle tree.

    Site blocks built by the Caddyfile adapter nest their handlers inside a `subroute`, so
    the headers and body limit have to be lifted out of it before the old proxies are
    dropped. `rewrite`/`reverse_proxy` are what this script replaces.
    """
    kept: list = []
    for h in handle or []:
        if h.get("handler") == "subroute":
            for inner in h.get("routes") or []:
                kept.extend(_flatten_handlers(inner.get("handle") or []))
            continue
        if h.get("handler") in ("reverse_proxy", "rewrite"):
            continue
        kept.append(h)
    return kept


def patch(config: dict) -> list[str]:
    """Rewrite the dubwarz.vip route in place; return notes about what was touched."""
    notes: list[str] = []
    for srv in config.get("apps", {}).get("http", {}).get("servers", {}).values():
        for route in srv.get("routes") or []:
            if not any(DOMAIN in h for h in hosts_of(route)):
                continue
            notes.append(f"matched site block {hosts_of(route)}")
            # The security headers (deferred) and the body limit only do their job when
            # they run *before* the proxy, so they stay in front of the routing subroute.
            kept = _flatten_handlers(route.get("handle") or [])
            route["handle"] = kept + [{"handler": "subroute",
                                       "routes": copy.deepcopy(AIGF_ROUTES)}]
            route["terminal"] = True
            notes.append("kept handlers: " + ", ".join(h.get("handler", "?") for h in kept))
            notes.append(f"/aigf + /aigf/api/* → {MODAL_HOST}")
            notes.append(f"everything else → {LOCAL_INDEX}")
    return notes


def get_config() -> dict:
    with urllib.request.urlopen(ADMIN + "/config/", timeout=10) as resp:
        return json.loads(resp.read().decode())


def load_config(config: dict) -> int:
    req = urllib.request.Request(
        ADMIN + "/load",
        data=json.dumps(config).encode(),
        method="POST",
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not (args.dry_run or args.apply):
        ap.error("pass --dry-run or --apply")

    live = get_config()
    notes = patch(live)
    if not notes:
        print("no dubwarz.vip site block found; refusing to write")
        return 1
    for note in notes:
        print(" -", note)

    if args.dry_run:
        print("dry run: nothing sent")
        return 0

    print("admin /load:", load_config(live))

    disk = json.loads(RUN_CONFIG.read_text(encoding="utf-8"))
    disk_notes = patch(disk)
    RUN_CONFIG.write_text(json.dumps(disk, indent=2), encoding="utf-8")
    print(f"wrote {RUN_CONFIG} ({len(disk_notes)} patch note(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
