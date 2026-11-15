# DUBWARZ — engineering, release and profit

The operating document for the JINXAI ORBITAL / DUBWARZ deployment: what runs, how it is
released, and the arithmetic that decides whether it should exist.

**Snapshot: 15 November 2026.** Every number in the cost tables was measured on this
deployment, not estimated; every "live" claim below was verified against the public domain at
the time of writing. Where something is not built, it says so.

---

## 1. Executive summary

| Question | Answer |
|---|---|
| What is it? | A portrait-and-motion render service branded **JINXAI ORBITAL**, with a public character gallery and adapter marketplace at **dubwarz.vip** |
| Where does it run? | A Modal app (`jinxai-orbital`) on H100s, fronted by a local Caddy reverse proxy |
| Is the domain live? | Yes — `https://dubwarz.vip` returns 200, valid certificate to 7 Feb 2027 |
| Can it take money? | **No.** Stripe keys are absent and checkout answers 503 by design |
| What is the unit cost? | $0.049 blended per frame, $0.0555 per reel, $0.0974 per frame+adapter |
| What is the price? | COMET $29 / PULSAR $99 / QUASAR $399 per month, 61–66% gross margin at full use |
| What breaks the business? | Removing the GPU container cap; selling "unlimited"; a mostly-cold traffic mix |
| What is live right now | 9 gallery characters (8 shipped + 1 distilled), 8 marketplace listings with rendered thumbnails, 1 node, Stripe unconfigured |

The engineering is further along than the commerce. The render path, gallery, federation,
marketplace and proxy all work; the payment rail ends at a 503 with instructions, which is the
honest state rather than a pretended charge.

---

## 2. Architecture

### 2.1 Shape

```
                     ┌──────────────────────────────────────────────┐
  dubwarz.vip  ──────►│ Caddy 2.11.9  (this host, :443 / :80 / :2019) │
  (A → WAN IP)       │  tls · Host pinned · / → /gallery             │
                     └───────────────────┬──────────────────────────┘
                                         │ https, Host: xzyorb--orbital…
                     ┌───────────────────▼──────────────────────────┐
                     │ Modal · jinxai-orbital · asgi "orbital"      │
                     │                                              │
                     │  web tier (web_image, max 3 containers)      │
                     │   ├─ /                studio UI (ui.html)    │
                     │   ├─ /gallery         public gallery          │
                     │   ├─ /api/gallery     global catalog          │
                     │   ├─ /api/girlfriend  prompt distillation     │
                     │   ├─ /api/market      signed listings         │
                     │   ├─ /api/econ        prices + split          │
                     │   └─ /api/net         peer table              │
                     └───────┬──────────────────────┬───────────────┘
                             │ .remote()            │ .remote() / .spawn()
                ┌────────────▼─────────┐   ┌────────▼──────────────┐
                │ Krea2 (image H100)   │   │ Krea2Video (video GPU)│
                │ 8-step distilled DiT │   │ Wan 2.2 5B / A14B     │
                │ 1024² · 640s idle→off│   │ 81 frames · 180s idle │
                └────────────┬─────────┘   └────────┬──────────────┘
                             │                      │
                     ┌───────▼──────────────────────▼───────┐
                     │ Volumes: weights, hf-cache           │
                     │ Dicts: peers, ledger, listings,       │
                     │        girlfriends                    │
                     └──────────────────────────────────────┘
```

### 2.2 Components

| Component | Where | Role | Key settings |
|---|---|---|---|
| `web` (ASGI) | `web_image` | UI, gallery, marketplace, econ, net routes | `max_containers=3`, `timeout=1800`, `scaledown_window=600` |
| `Krea2` class | `image` on H100 | image render, 8-step distilled core | `cuda` H100, 64 GB RAM, `scaledown 300 s` |
| `Krea2LoRA` class | `image` on H100 | per-image adapter fit | spawned so the fit overlaps the render |
| `Krea2Video` class | `image` on video GPU | Wan 2.2 image→video, 32-frame clips stitched | 192 GB RAM, `scaledown 180 s` |
| `stitch` | `image`, CPU | concat clips to one mp4 | `cpu=4`, `memory=8192` |
| `store_lora_token`, `download_*` | `image`, CPU | adapter fetch and token storage | volume-backed |
| Caddy | this host | TLS, domain, path rewrite, Host pinning | config `tools/caddy/run_config.json` |
| Modal Volumes | cloud | weights (5.7 GB), HF cache | ~$0.51/month for the LoRA volume |
| Modal Dicts | cloud | peers, ledger, listings, distilled characters | survive deploys, expire peers after 15 min |

### 2.3 Request paths

- **A render** — browser → Caddy (TLS) → web tier → `Krea2.generate.remote()` → H100 → PNG
  returned as base64 in the same HTTP response. A per-image adapter fit is `.spawn()`ed in
  parallel, so the caller does not wait for both.
- **The gallery** — `/gallery` is a static page; it fetches `/api/gallery`, which merges the
  shipped catalog, distilled characters, and every peer's `?scope=local` catalog, deduped by
  `sku` with the local copy winning.
- **A distilled character** — `POST /api/girlfriend/distill` classifies the prompt offline,
  stores the manifest in the `orbital-girlfriends` Dict, and returns the card.
- **A purchase (blocked)** — `/api/shop/checkout` builds a Stripe destination charge; with no
  keys it returns 503 and charges nothing.

---

## 3. Code map

| File | Responsibility |
|---|---|
| `app.py` | the whole Modal app: images, GPU classes, web routes, state wiring (~3,000 lines) |
| `orbit_net.py` | peer table (normalise/merge/pick), HTTP forward, gallery merge helper |
| `orbit_econ.py` | integer-cent pricing, split, Stripe destination-charge parameters |
| `orbit_market.py` | signed listing manifests, validation, merge, file digests |
| `orbit_girls.py` | prompt → character distillation, tag/group classification, summaries |
| `gallery/index.html` | public gallery page (cards, distill bar, filter) |
| `gallery/catalog.json` | the eight shipped characters |
| `market/build_listings.py`, `market/listings/*.json` | marketplace seed manifests |
| `ui.html` | the studio UI served at `/` |
| `tools/render_thumbs.py` | renders a frame per listing and PUTs it to the volume |
| `tools/caddy_point_at_orbital.py` | repoints `dubwarz.vip` via Caddy's admin API |
| `tools/caddy_build_run_config.py` | rebuilds the runnable Caddy config from autosave |
| `tools/caddy/` | Caddy binary, run config, elevated restart script |
| `launch/*.md` | brand, pricing, economics, network, marketplace, release notes |
| `comfy/orbital_*.json` | the host-side node graphs that mirror the pipeline |
| `AGENTS.md` | deploy discipline and current-state rules for agents |

---

## 4. Engineering

### 4.1 Pipeline, as configured

| Parameter | Value | Why |
|---|---|---|
| Image resolution | 1024 px, aspect presets `1:1 4:5 3:4 16:9 9:16` | multiples of 16; latent patch 8×2 |
| Sampling | 8 steps, Euler/simple, CFG 0–1.0 | distilled core; negative branch unused |
| Adapter fit | rank 8, 40 steps, per image | runs in parallel with the render |
| Motion | Wan 2.2 TI2V-5B (fast) or I2V-A14B (28B MoE) | A14B swaps experts over PCIe, 126 GB on disk |
| Video length | 32-frame clips, stitched to 81 frames | mixed-length clips normalised to the largest |
| Cold start | weight load dominates (~60 s of the 92 s) | why cold frames cost 3× warm |

### 4.2 Measured performance and cost

H100 SXM5 at **$0.001097/GPU-second = $3.9492/hour**, billed per second (Modal, Nov 2026).

| Operation | Warm | Cold | Warm cost | Cold cost |
|---|---|---|---|---|
| Frame, 8 steps, 1024² | 33 s | 92 s | $0.0362 | $0.1009 |
| Frame + adapter imprint | 77 s | — | $0.0845 | — |
| Imprint alone | 44 s | — | $0.0483 | — |
| Reel, 81 frames | 39 s | 97 s | $0.0428 | $0.1064 |

Price against the **blended** figure (80% warm / 20% cold): frame **$0.0491**, frame+imprint
**$0.0974**, reel **$0.0555**. Low traffic means most requests pay a cold start, so any pricing
built on warm-only numbers is fiction.

### 4.3 Idle is the real cost

Idle windows: image worker 300 s, video worker 180 s, web tier 600 s. A single late-night
render can cost more in idle time than in compute. The `max_containers=1` cap on GPU workers is
also the abuse ceiling for a public URL:

```
1 image H100 + 1 video H100 = $7.90/hr  ≈ $190/day   # if someone loops the endpoint
no cap (Starter's 10-GPU concurrency)  ≈ $948/day
```

### 4.4 Failure modes already paid for

| Symptom | Root cause | Fix in place |
|---|---|---|
| Every GPU/class container dies at startup; `/health` still 200 | Modal 1.6.4 does not implicitly mount sibling modules | `.add_local_python_source(...)` on **both** images — including every new helper module |
| Deploy says success, responses keep the old JSON shape for minutes | previous revision's containers keep serving (`scaledown_window=600`) | confirm with a changed field; `modal app stop` only when flushing is genuinely required |
| `dubwarz.vip` dead while the app was healthy | wedged Caddy (SYSTEM, session 0) refused all traffic and its admin API | local Caddy 2.11.9 + `tools/caddy/run_config.json`, restart elevated |
| Global gallery reported itself as a failed peer | heartbeat self-registration with the `unset.invalid` sentinel | skip self by node id, by URL, and the sentinel |
| Marketplace listed 3 of 8 adapters | seed dict populated once, then only live state | seed manifests merged on every read |

### 4.5 Deploy discipline (see `AGENTS.md`)

A restart is not a verification tool. Batch all changes, check locally, deploy once, verify with
read-only GETs. No GPU re-renders "to be sure". Caddy is restarted only for a config change or a
wedge.

### 4.6 Security posture

| Surface | State |
|---|---|
| Access gate | `ORBITAL_ACCESS_KEY` supported on mutating routes; currently **off** |
| Cluster auth | `ORBITAL_CLUSTER_TOKEN` shared secret; unset, so peers are open |
| Stripe | keys read from env only; absent, so checkout 503s |
| Civitai token | volume file, mode 0600, never returned by any route |
| Marketplace signing | `MARKET_SECRET` unset → `signed: false` on all listings |
| Public exposure | every route is reachable without auth; the GPU cap is the spend ceiling |

---

## 5. API surface

| Group | Routes |
|---|---|
| Health | `GET /health`, `GET /api/info` |
| Render | `POST /api/generate`, `POST /api/video` |
| Adapters | `POST /api/lora/{fit,download,download_all,token}`, `GET /api/lora/token`, `GET /api/lora/{job}` |
| Gallery | `GET /gallery`, `GET /api/gallery[?scope=local]` |
| Distilled | `GET /api/girlfriends`, `POST /api/girlfriend/distill` |
| Marketplace | `GET /api/market/listings`, `GET /api/market/listing/{slug}`, `GET|PUT /api/market/thumb/{slug}`, `POST /api/market/{publish,announce}` |
| Commerce | `POST /api/shop/checkout`, `POST /api/econ/checkout`, `GET /api/econ/{terms,ledger}`, `POST /api/econ/record` |
| Network | `GET /api/net/peers`, `POST /api/net/{register,gossip,generate}` |

Live behaviour today: `/api/gallery` → 9 characters (1 distilled); `/api/market/listings` →
8, `signed: false`; `/api/econ/terms` → `developer_bps: 7000, stripe_configured: false`;
`/api/net/peers` → 1 node.

---

## 6. Release runbook

### 6.1 Deploy

```bash
export MODAL_TOKEN_ID="…"
./.venv/Scripts/python.exe -c "import ast,pathlib; ast.parse(pathlib.Path('app.py').read_text(encoding='utf-8'))"
./.venv/Scripts/modal.exe deploy app.py
```

Every module imported by `app.py` must be listed in `.add_local_python_source(...)` on both
`image` and `web_image`. Missing that fails only the GPU tiers, so `/health` keeps lying.

### 6.2 Verify (read-only)

```bash
B=https://dubwarz.vip
curl -s $B/health
curl -s $B/api/gallery | python -c "import json,sys;d=json.load(sys.stdin);print(d['count'],d.get('distilled'))"
curl -s $B/api/market/listings | python -c "import json,sys;print(json.load(sys.stdin)['count'])"
curl -s $B/api/econ/terms
```

### 6.3 Domain

`dubwarz.vip` resolves to `107.179.232.160`, which is this host's public IP; Caddy terminates
TLS and proxies to the Modal endpoint, rewriting only `/` to `/gallery`.

- Config: `tools/caddy/run_config.json` (built from autosave + repoint, admin on `127.0.0.1:2019`)
- Rebuild: `python tools/caddy_build_run_config.py`
- Restart (needs elevation): `tools/caddy/restart_caddy.bat`
- Certificate: `CN=dubwarz.vip`, valid to **7 Feb 2027**; renewal needs 80/443 reachable
- Caveat: Caddy runs in the interactive session, so it does **not** survive a reboot or logoff
  until it is registered as a service or a startup task

### 6.4 Release checklist

- [ ] Local parse + unit checks pass
- [ ] One deploy, not several
- [ ] `/health` reports the expected app and GPU
- [ ] `/` and `/gallery` return 200 through `https://dubwarz.vip` (not just the Modal URL)
- [ ] `/api/gallery` count and `distilled` field are sane
- [ ] No secret in source: `grep -rn "sk_live\|acct_\|civitai" .` clean
- [ ] Caddy admin answers 200 (so a future reload needs no restart)

### 6.5 Known gaps before real money moves

1. **No payment-to-entitlement.** Checkout creates a PaymentIntent; nothing consumes
   `payment_intent.succeeded`, so no sale unlocks a download.
2. **No download route.** `/api/lora/{job}/file` serves a fitted adapter by URL; a paid SKU
   needs `/api/market/download/{slug}` with digest + entitlement checks.
3. **No moderation and no age gate.** A public gallery accepting arbitrary prompts needs both,
   plus a written policy, before it can host risk-bearing content.
4. **Marketplace unsigned.** `MARKET_SECRET` unset; manifests are integrity-checked by nothing.
5. **No persistence of renders.** The gallery shows what is in the catalog, not user history.

---

## 7. Profit

### 7.1 Cost floor

| Unit | Blended cost |
|---|---|
| Frame | $0.0491 |
| Frame + imprint | $0.0974 |
| Reel | $0.0555 |

A 3× floor covers retries and review time: **$0.15/frame**, **$0.29/imprint**, **$0.20/reel** —
exactly the overage prices the code charges (`orbit_econ.PRICES`).

### 7.2 Subscription tiers

Monthly, prepaid, 10× for annual (two months free).

| | COMET | PULSAR | QUASAR |
|---|---|---|---|
| Price | **$29** | **$99** | **$399** |
| Frames | 200 | 700 | 2,500 |
| Imprints | — | 10 | 40 |
| Reels | — | 30 | 120 |
| Cost at full use | $9.83 | $37.04 | $133.42 |
| Net after Stripe (2.9% + 30¢) | $27.86 | $95.83 | $387.13 |
| **Gross margin** | **64.7%** | **61.3%** | **65.5%** |
| Contribution | $18.03 | $58.79 | $253.71 |

Each cap is priced at 3× blended cost at full use; margin grows as utilisation falls, which is
the point of a cap. Below ~2.5× a customer who uses what they paid for loses money.

### 7.3 Break-even

| Fixed cost, monthly | Amount |
|---|---|
| Modal Team tier (needed for a branded domain) | $250.00 |
| Domain | ~$1.00 |
| **Total** | **≈ $251.00** |

| Tier | Contribution | Subscribers to cover $251 |
|---|---|---|
| COMET | $18.03 | 14 |
| PULSAR | $58.79 | **5** |
| QUASAR | $253.71 | **1** |

**Five PULSAR subscribers pay for the platform tier.** On the free tier fixed cost is $0 and
the app is profitable from the first subscriber, at the cost of a vendor-shaped URL.

### 7.4 Marketplace arithmetic

At $19 a pack with the 70% developer share:

```
gross                  $19.00
developer (70%)        $13.30     paid to the connected account on capture
platform (30%)          $5.70     before Stripe's 2.9% + 30¢
Stripe fee on $19       $0.85
platform net            $4.85
```

The adapter itself costs ~44 s of H100 (~$0.048) to fit, so price is set by the character, not
by compute — the argument for packs over per-image credits.

**Destination-charge floor.** With `application_fee_amount`, the platform carries the card fee
on the whole gross:

```
platform_cents ≥ 0.029 × gross + 30
```

At a 30% platform share that means **gross ≥ ≈$1.35** before the platform clears anything. A
$1 item at 70% to the developer loses money on every sale. Price per-charge items above $1.35
or sell subscriptions.

### 7.5 What makes this unprofitable

| Risk | Effect |
|---|---|
| Removing `max_containers=1` | public loop can reach ~$948/day |
| Selling "unlimited" | blended cost is linear; unlimited bets against customers |
| Mostly-cold traffic (>50%) | a frame costs ~$0.10 and COMET loses money |
| Both GPU workers warm all day | $7.90/hr regardless of usage |
| Adult SKUs through Stripe | account and funds at risk; needs a high-risk processor (5–15% all-in) |
| Counting marketplace points as revenue | the point-to-dollar rate is unpublished; treat as $0 |

### 7.6 Measurable gates

| Window | Gate | Pass / fail action |
|---|---|---|
| 30 days | ≥100 valid renders on the public app | if not, the listing copy or thumbnail is the problem, not the model |
| 30 days | first paying subscriber after Stripe is enabled | if none, the price or the demo is wrong |
| 60 days | ≥5 PULSAR-equivalent subscribers | covers the platform tier; below it, stay on free tier and keep the URL |
| 90 days | marketplace revenue > $0 with a settled statement | otherwise keep listings as showcase only |
| Continuous | cold-start share < 50% | above it, raise `KREA2_SCALEDOWN` and re-price |

---

## 8. Open decisions

1. **Payment rail** — Stripe destination charges are wired but unkeyed. Enabling needs
   `STRIPE_SECRET_KEY`, `STRIPE_CONNECTED_ACCOUNT`, and a webhook that writes entitlement.
2. **Branded domain cost** — the platform tier is $250/month; the domain currently works on the
   free tier only because Caddy fronts it locally. A reboot without a Caddy service takes the
   site down.
3. **Auto-distillation** — distilling every `/api/generate` prompt would publish arbitrary
   visitor text; today distillation is an explicit gallery action.
4. **Moderation** — required before the gallery hosts anything beyond the shipped SFW set.
5. **Cluster growth** — a second node is one `ORBITAL_PEERS` + `ORBITAL_CLUSTER_TOKEN` deploy
   away, and that is what turns the global gallery from a single-node view into a network.

---

## 9. Appendix — environment variables

| Variable | Default | Effect |
|---|---|---|
| `KREA2_GPU` | `H100` | image worker GPU (`A100-80GB` halves hourly) |
| `KREA2_SCALEDOWN` | `300` | image idle seconds before stop |
| `KREA2_VIDEO_GPU` / `KREA2_VIDEO_SCALEDOWN` | GPU / `180` | video worker |
| `ORBITAL_NODE_ID` | `orbital` | this node's name in the peer table |
| `ORBITAL_SELF_URL` | empty | public URL peers should call |
| `ORBITAL_PEERS` | empty | comma-separated seed peers |
| `ORBITAL_CLUSTER_TOKEN` | empty | shared secret for peer routes |
| `ORBITAL_ACCESS_KEY` | empty | when set, guards mutating routes |
| `ORBITAL_CUSTOM_DOMAIN` | empty | Modal custom domain at deploy (Team/Enterprise) |
| `STRIPE_SECRET_KEY` / `STRIPE_CONNECTED_ACCOUNT` | empty | enables checkout |
| `STRIPE_DEVELOPER_BPS` | `7000` | developer share in basis points |
| `MARKET_SECRET` | empty | signs marketplace manifests |

---

*Sources of truth: `app.py` and the `orbit_*.py` modules for behaviour; `launch/pricing.md` and
`launch/economics.md` for the measured costs; `AGENTS.md` for operating rules. If this document
and the code disagree, the code is right and this file is a bug.*
