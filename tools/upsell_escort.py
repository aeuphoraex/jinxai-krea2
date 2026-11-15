"""Build one roster member's upgraded AIGF gallery from her roster photos.

An escort page (`dubwarz.vip/#/a/sophia`) shows 1-3 frames of her. This sends those same
photos to the ORBITAL backend, where the unfiltered VLM recognises her and img2img upgrades
each frame to 2K — thumb 1K, zoom 2K, download 4K — so the page can replace its originals
with renders that are recognisably *her*.

    python tools/upsell_escort.py dw-kessa photo1.jpg photo2.jpg --scene "hangar bay" --4k

Writes `<out>/<sku>-{thumb,zoom,download}.png` and `<out>/<sku>.json`. The endpoint also
stores the 1K frame as her stock thumbnail, so her gallery card updates on its own.

Auth: set ORBITAL_ACCESS_KEY if the deployment gates writes (it is a no-op when unset).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import urllib.error
import urllib.request

BASE = os.environ.get("ORBITAL_URL", "https://dubwarz.vip/aigf").rstrip("/")
KEY = os.environ.get("ORBITAL_ACCESS_KEY", "").strip()


def _b64(path: str) -> str:
    data = pathlib.Path(path).read_bytes()
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sku", help="character sku, e.g. dw-kessa")
    ap.add_argument("images", nargs="+", help="1-3 roster photos of her")
    ap.add_argument("--scene", default="", help="optional setting to place her in")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--strength", type=float, default=None,
                    help="img2img strength; low keeps her face, high follows the prompt")
    ap.add_argument("--4k", action="store_true", help="also render the 4K download frame")
    ap.add_argument("--out", default="out/upsell", help="where to write the frames")
    ap.add_argument("--timeout", type=float, default=900.0)
    args = ap.parse_args()

    if not 1 <= len(args.images) <= 3:
        ap.error("give 1-3 source images")

    payload = {
        "images": [_b64(p) for p in args.images],
        "scene": args.scene,
        "download_4k": args.__dict__["4k"],
    }
    if args.seed is not None:
        payload["seed"] = args.seed
    if args.strength is not None:
        payload["strength"] = args.strength

    req = urllib.request.Request(
        f"{BASE}/api/upsell/{args.sku}",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"content-type": "application/json",
                 **({"x-access-key": KEY} if KEY else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read().decode()[:400]}")
        return 1

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in (body.get("renders") or {}).items():
        if not data:
            continue
        path = out / f"{args.sku}-{name}.png"
        path.write_bytes(base64.b64decode(data.split(",", 1)[-1]))
        written.append(f"{path} ({path.stat().st_size / 1e6:.2f} MB)")
    (out / f"{args.sku}.json").write_text(
        json.dumps(body.get("record") or {}, indent=2), encoding="utf-8")

    print(f"description: {(body.get('record') or {}).get('description', '')[:200]}")
    adapters = body.get("adapters") or []
    print("adapters:", ", ".join(f"{a.get('name')} @ {a.get('scale')}" for a in adapters) or "none")
    print(f"seconds: {body.get('seconds')}")
    for line in written:
        print("wrote", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
