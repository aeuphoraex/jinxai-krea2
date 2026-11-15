"""Point dubwarz.vip at the deployed ORBITAL endpoint through Caddy's admin API.

Caddy runs on this box with its admin endpoint on 127.0.0.1:2019 (the autosave file still
says `admin: None`, which is stale — the listener is real). Reloading through the API keeps
every other site block (xzyorbitz, jinxai, dubwarz.club, hdc...) exactly as it was.

What it changes, only inside the dubwarz.vip / www.dubwarz.vip route:

  * reverse_proxy upstream   127.0.0.1:8792  ->  xzyorb--orbital.us-east.modal.run:443
  * transport                http + tls (Modal terminates TLS at its edge)
  * request Host header      the Modal hostname (Modal routes on Host)
  * a rewrite for `/` only   -> /gallery, so the domain lands on the shop

    python tools/caddy_point_at_orbital.py --dry-run
    python tools/caddy_point_at_orbital.py --apply
"""

from __future__ import annotations

import argparse
import json
import urllib.request

ADMIN = "http://127.0.0.1:2019"
TARGET_HOST = "xzyorb--orbital.us-east.modal.run"
DOMAIN = "dubwarz.vip"


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


def hosts_of(route: dict) -> list[str]:
    out: list[str] = []
    for matcher in route.get("match") or []:
        for host in matcher.get("host") or []:
            out.append(str(host))
    return out


def patch(config: dict) -> tuple[int, list[str]]:
    notes: list[str] = []
    hits = 0
    for srv in config.get("apps", {}).get("http", {}).get("servers", {}).values():
        for route in srv.get("routes", []):
            if not any(DOMAIN in h for h in hosts_of(route)):
                continue
            notes.append(f"matched site block {hosts_of(route)}")
            for handler in route.get("handle", []):
                routes = handler.get("routes")
                if not routes:
                    continue
                rebuilt: list[dict] = []
                for sub in routes:
                    if any(h.get("handler") == "reverse_proxy" for h in sub.get("handle", [])):
                        for h in sub.get("handle", []):
                            if h.get("handler") != "reverse_proxy":
                                continue
                            before = [u.get("dial") for u in h.get("upstreams", [])]
                            h["upstreams"] = [{"dial": f"{TARGET_HOST}:443"}]
                            h["transport"] = {"protocol": "http", "tls": {}}
                            h.setdefault("headers", {}).setdefault("request", {}).setdefault(
                                "set", {}
                            )["Host"] = [TARGET_HOST]
                            notes.append(f"upstream {before} -> {TARGET_HOST}:443 (tls)")
                            hits += 1
                        rebuilt.append({
                            "match": [{"path": ["/"]}],
                            "handle": [{"handler": "rewrite", "uri": "/gallery"}],
                        })
                        notes.append("rewrite: / -> /gallery")
                    rebuilt.append(sub)
                handler["routes"] = rebuilt
    return hits, notes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = get_config()
    hits, notes = patch(config)
    for note in notes:
        print(" -", note)
    if not hits:
        print("no dubwarz.vip reverse_proxy route found; nothing changed")
        return 1
    if args.apply:
        print("load ->", load_config(config))
    else:
        print("dry run; pass --apply to load")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
