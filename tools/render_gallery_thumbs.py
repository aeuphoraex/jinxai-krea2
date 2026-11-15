"""Render a stock frame for every gallery character and store it in the volume.

The gallery ships characters with a prompt and a seed but no image, so a visitor sees empty
cards. This walks the catalog (and optionally the distilled characters), renders one frame
through the same `/api/generate` path a visitor uses, and PUTs the PNG to
`/api/gallery/thumb/{sku}`. The page then shows the stock frame until someone hits Generate.

    python tools/render_gallery_thumbs.py                 # shipped catalog, all of it
    python tools/render_gallery_thumbs.py --only dw-kessa # one character
    python tools/render_gallery_thumbs.py --include-distilled
    python tools/render_gallery_thumbs.py --force         # re-render even if one exists

Skips anything that already has a thumbnail, so re-running is cheap and resumable.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "https://xzyorb--orbital.us-east.modal.run"

ASPECTS = {"1:1": (1024, 1024), "4:5": (816, 1024), "3:4": (768, 1024),
           "16:9": (1024, 576), "9:16": (576, 1024)}


def get(path: str, timeout: float = 60.0) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def thumb_exists(sku: str) -> bool:
    req = urllib.request.Request(f"{BASE}/api/gallery/thumb/{sku}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as exc:
        return exc.code != 404
    except Exception:
        return False


def size_for(aspect: str) -> tuple[int, int]:
    if aspect in ASPECTS:
        return ASPECTS[aspect]
    try:
        w, h = (int(x) for x in str(aspect).split(":"))
    except Exception:
        return ASPECTS["4:5"]
    return (max(16, round(1024 * w / h / 16) * 16), 1024)


def render(card: dict) -> bytes:
    width, height = size_for(str(card.get("aspect") or "4:5"))
    payload = {
        "prompt": card["prompt"],
        "seed": int(card.get("seed") or 0),
        "width": width,
        "height": height,
        "steps": 8,
        "cfg": 0,
        "count": 1,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + "/api/generate", data=body,
                                 headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=900) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    if out.get("error"):
        raise RuntimeError(out["error"])
    images = out.get("images") or []
    if not images:
        raise RuntimeError("no image returned")
    return base64.b64decode(images[0]["data"])


def store(sku: str, png: bytes) -> dict:
    req = urllib.request.Request(f"{BASE}/api/gallery/thumb/{sku}", data=png,
                                 headers={"content-type": "image/png"}, method="PUT")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="", help="one sku")
    parser.add_argument("--include-distilled", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    local = get("/api/gallery?scope=local")
    cards = list(local.get("characters") or [])
    if args.include_distilled:
        cards += list(get("/api/girlfriends").get("characters") or [])
    if args.only:
        cards = [c for c in cards if c.get("sku") == args.only]
        if not cards:
            print(f"no character with sku {args.only!r}")
            return 1

    print(f"{len(cards)} character(s) to consider")
    done = skipped = failed = 0
    for i, card in enumerate(cards, 1):
        sku = str(card.get("sku"))
        if not args.force and thumb_exists(sku):
            print(f"[{i}/{len(cards)}] {sku}: stock frame already present, skipping")
            skipped += 1
            continue
        started = time.time()
        try:
            png = render(card)
            result = store(sku, png)
            print(f"[{i}/{len(cards)}] {sku}: {result['bytes']} bytes "
                  f"in {time.time() - started:.0f}s")
            done += 1
        except Exception as exc:
            print(f"[{i}/{len(cards)}] {sku}: FAILED {type(exc).__name__}: {exc}"[:300])
            failed += 1
    print(f"\nrendered {done}, skipped {skipped}, failed {failed}")
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
