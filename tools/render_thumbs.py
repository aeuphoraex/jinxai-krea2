"""Render one real frame per marketplace listing and store it as the thumbnail.

Drives the deployed API rather than the model directly, so it uses exactly the settings a
buyer's "Generate" button would:

    python tools/render_thumbs.py                       # every listing that has a prompt
    python tools/render_thumbs.py --only dw-kessa       # one
    python tools/render_thumbs.py --force               # redo existing thumbnails

Each frame is POSTed to /api/generate, then the returned PNG is PUT to
/api/market/thumb/<slug>. If ORBITAL_ACCESS_KEY is set on the deployment, export the same
value before running so the upload is authorised.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

DEFAULT_URL = "https://xzyorb--orbital.us-east.modal.run"
LISTINGS = pathlib.Path(__file__).resolve().parent.parent / "market" / "listings"


def size_for(aspect: str) -> tuple[int, int]:
    try:
        w, h = (float(x) for x in str(aspect or "4:5").split(":"))
    except ValueError:
        w, h = 4.0, 5.0
    width = round(1024 * w / h / 16) * 16
    return max(512, width), 1024


def post_json(url: str, payload: dict, token: str, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    if token:
        req.add_header("x-access-key", token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def put_png(url: str, blob: bytes, token: str, timeout: float) -> dict:
    req = urllib.request.Request(url, data=blob, method="PUT",
                                 headers={"content-type": "image/png"})
    if token:
        req.add_header("x-access-key", token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("ORBITAL_URL", DEFAULT_URL))
    parser.add_argument("--only", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    base = args.url.rstrip("/")
    token = (os.environ.get("ORBITAL_ACCESS_KEY") or "").strip()
    files = sorted(LISTINGS.glob("*.json"))
    if args.only:
        files = [p for p in files if p.stem == args.only]
    if args.limit:
        files = files[: args.limit]
    if not files:
        print("no listings matched")
        return 1

    done, skipped, failed = 0, 0, 0
    for path in files:
        listing = json.loads(path.read_text(encoding="utf-8"))
        slug = listing.get("slug") or path.stem
        prompt = (listing.get("prompt") or "").strip()
        if not prompt:
            print(f"[skip] {slug}: no prompt in the listing")
            skipped += 1
            continue
        if not args.force:
            try:
                probe = urllib.request.Request(f"{base}/api/market/thumb/{slug}", method="HEAD")
                with urllib.request.urlopen(probe, timeout=20) as resp:
                    length = int(resp.headers.get("content-length") or 0)
                if length > 60_000:
                    print(f"[skip] {slug}: thumbnail already {length} bytes")
                    skipped += 1
                    continue
            except Exception:
                pass

        width, height = size_for(listing.get("aspect"))
        payload = {
            "prompt": prompt,
            "seed": int(listing.get("seed") or 0),
            "width": width,
            "height": height,
            "steps": 8,
            "cfg": 0,
            "count": 1,
        }
        started = time.time()
        try:
            out = post_json(f"{base}/api/generate", payload, token, timeout=600)
            if out.get("error"):
                raise RuntimeError(out["error"])
            first = (out.get("images") or [{}])[0]
            blob = base64.b64decode(first.get("data") or "")
            if len(blob) < 10_000:
                raise RuntimeError(f"render too small ({len(blob)} bytes)")
            saved = put_png(f"{base}/api/market/thumb/{slug}", blob, token, timeout=120)
            print(f"[ok] {slug}: {len(blob)} bytes in {time.time() - started:.1f}s -> {saved.get('path')}")
            done += 1
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, ValueError) as exc:
            print(f"[fail] {slug}: {type(exc).__name__}: {exc}")
            failed += 1
    print(f"done={done} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
