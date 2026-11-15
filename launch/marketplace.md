# Adapter marketplace (decentralised listings)

Each adapter is sellable as a signed manifest. There is no marketplace database: nodes keep
the manifests they have seen and replicate them to peers, and `signed_at` decides which copy
wins. Thumbnails are generated from the slug, so a listing ships with no image assets.

## Routes

| Route | Auth | Does |
|---|---|---|
| `GET /api/market/listings` | none | every known manifest + `signed` flag |
| `GET /api/market/listing/{slug}` | none | one manifest |
| `GET /api/market/thumb/{slug}` | none | 640×800 deterministic PNG |
| `POST /api/market/publish` | cluster token | validate, sign, store one manifest |
| `POST /api/market/announce` | cluster token | store, then push to every known peer |
| `POST /api/shop/checkout` | none | destination charge for a `sku` |

`market/listings/*.json` is the seed set baked into the image; the first read copies it into
the `orbital-listings`-style dict (`market_listings`) and everything after that is live state.

## A listing

```json
{
  "slug": "dw-kessa",
  "name": "Kessa — dock pilot",
  "seller": "JINXAI",
  "price_cents": 1900,
  "currency": "usd",
  "description": "...",
  "thumb": "/api/market/thumb/dw-kessa",
  "weights": "per_image/kessa.safetensors",
  "weights_sha256": "<64 hex>",
  "signed_at": 1794900000,
  "rating": "sfw"
}
```

`weights_sha256` is optional but should be filled before anyone pays: it is the only thing
that ties the manifest to the bytes a buyer receives. Compute it with
`orbit_market.digest_file(path)`.

## Selling flow

```bash
# 1. publish locally
curl -X POST "$URL/api/market/publish" -H "x-cluster-token: $CLUSTER" \
  -H 'content-type: application/json' -d @market/listings/dw-kessa.json

# 2. spread it to every peer the node knows
curl -X POST "$URL/api/market/announce" -H "x-cluster-token: $CLUSTER" \
  -H 'content-type: application/json' -d @market/listings/dw-kessa.json

# 3. buy it
curl -X POST "$URL/api/shop/checkout" -H 'content-type: application/json' \
  -d '{"sku":"dw-kessa"}'
```

Without Stripe keys step 3 returns **503** and charges nothing.

## Why this is "decentralised", precisely

- **No shared database.** A node that has never spoken to another still has its own listings.
- **Replication by push.** `announce` sends the manifest to each peer's `publish`, which
  validates and merges it. Two hops is enough for a three-node cluster; it is not a DHT.
- **Convergence rule.** Newest `signed_at` wins per slug; merging the same manifest twice is a
  no-op. No vector clocks, no conflict UI.
- **Integrity, not trustlessness.** An HMAC with `MARKET_SECRET` plus a sha256 of the weights
  catches corruption and casual forgery. It does **not** stop a node holding the secret, and
  there is no public-key identity. Calling this trustless would be false.

## Not built yet (and what it costs to build)

1. **Payment-to-entitlement.** Checkout creates a PaymentIntent; nothing listens for
   `payment_intent.succeeded`. Until a webhook writes `paid:<slug>` and the download checks
   it, every sale is honour-system. This is the first thing to build before charging.
2. **Download route.** `/api/lora/{job}/file` serves a fitted adapter; a marketplace needs
   `/api/market/download/{slug}` that verifies the sha, checks entitlement, and streams from
   the volume.
3. **Search and ranking.** Listings are returned alphabetically. With 20 items that is fine;
   at 200 it is not.
4. **Moderation.** Nothing vets a manifest. On a public domain with adult SKUs you need a
   review step and an age gate, not just a `rating` field.
5. **Thumbnails are placeholders.** They are deterministic gradients, not renders. A real
   listing wants one generated frame per adapter — the renderer already exists, so this is a
   loop, not a feature.

## Pricing guidance

At $19 with the 70% developer share, the seller's connected account receives $13.30 on
capture and the platform keeps $5.70 before Stripe's fee. Since a fitted adapter costs ~44 s
of H100 (~$0.048) to produce, pricing is set by the character, not the compute — which is the
whole argument for selling packs instead of per-image credits.
