"""Turn gallery/catalog.json into one signed-ready listing per character.

The thumbnail route renders from `prompt` + `seed` + `aspect`, so those three travel with
the listing. Re-run after editing the catalog:

    python market/build_listings.py
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
CATALOG = ROOT.parent / "gallery" / "catalog.json"
OUT = ROOT / "listings"


def main() -> int:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    written = 0
    for index, item in enumerate(catalog.get("characters", [])):
        sku = item["sku"]
        listing = {
            "slug": sku,
            "name": f"{item['name']} — {item['tag']}",
            "seller": "JINXAI",
            "price_cents": int(item.get("price_cents") or 0),
            "currency": catalog.get("currency", "usd"),
            "tier": "character",
            "description": item.get("tagline", ""),
            "prompt": item.get("prompt", ""),
            "seed": int(item.get("seed") or 0),
            "aspect": item.get("aspect", "4:5"),
            "thumb": f"/api/market/thumb/{sku}",
            "weights": f"per_image/{sku.split('-', 1)[-1]}.safetensors",
            "weights_sha256": "",
            "capabilities": ["image"],
            "signed_at": 1794900000 + index,
            "rating": item.get("rating", "sfw"),
        }
        (OUT / f"{sku}.json").write_text(
            json.dumps(listing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        written += 1
    print(f"wrote {written} listings to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
