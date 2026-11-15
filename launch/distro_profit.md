# Five hosts, two products, one quality bar

How the Krea 2 / ORBITAL deck stops being a Modal-only business: which host draws what, what
QA is worth in cents, and how a credit membership is priced so it cannot lose money when a
customer uses it.

**Snapshot: 15 November 2026.** Every platform figure below is dated and marked verified or
not. Costs on this deck are the measured ones in `launch/economics.md`, not estimates. Where
something is not built, it says so — the code is the source of truth and this file is a bug
when they disagree.

---

## 1. The finding

Two things decide whether this can be sold on five hosts, and the GPU rate is the second one.

1. **Permission, not price, is the binding constraint.** Hugging Face prohibits non-consensual
   sexual content and sexual deepfakes; RunningHub filters NSFW; MimicPC takes a *sublicensable,
   transferable* licence over everything you generate on it. Only Modal and RunPod can carry the
   adult deck. That splits the catalogue into two products with two price lists — and it is why
   "release everything everywhere" is not available at any price.
2. **Modal is the most expensive host per frame by 3–25×.** At 1 MP it is ~3.7c; a 24 GB
   RunPod worker is ~0.6c and a RunningHub second is ~0.1–0.3c at the reported coin rate. What
   Modal actually has is the *pipeline* — per-image adapter fitting, the 4K tile refine, Wan
   video, the character adapter registry. Those are the things no other host can run.

So the shape of the business is not "port the app four times". It is:

```
commodity frames  → cheapest permitted host (RunPod / RunningHub / MimicPC)
signature frames  → Modal (adapter fit, 4K, video, her identity)
quality + proof   → wherever the encoder already lives (Modal warm, HF ZeroGPU)
storefronts       → HF Space (SFW), RunningHub app (SFW), dubwarz.vip (both)
```

**A defect found and fixed:** `deploy/huggingface/app.py` used to post to `/aigf/api/generate`,
and that route hard-sets `variant=uncensored`, `encoder=abliterated` and `want_lora=True`
(`AGENTS.md`, locked contract) — a public HF front desk in front of the *unfiltered* deck, always
wearing the house `bimbo`/`breastshape` pair. The Space now submits directly to the RunPod
public worker (`deploy/runpod/handler.py`): stock core, no adapters, `orbit_guard`'s age rule
enforced before any GPU work. No Space traffic reaches the studio deck.

---

## 2. Platform truth table

| Host | Billing unit | Rate (1 MP frame ≈) | Idle cost | Effective $/GPU-h | Verified |
|---|---|---|---|---|---|
| **Modal** H100 | per second | $0.001097/s → **3.7c** | 300 s warm after the last order | **$3.95** | yes, this deck |
| **RunPod** serverless 24 GB | per second, scale to zero | $0.75/h → **0.6c** + 45 s load | none | **$0.75** | yes, runpod.io Nov 2026 |
| **RunPod** serverless H100 | per second | $5.39/h → 5.0c | none | $5.39 | yes |
| **RunPod** Community pod 4090 | per hour | $0.34/h | the pod, until stopped | $0.34 | yes |
| **RunPod** serverless 96 GB (RTX 6000 Pro) | per second | $3.49/h | none | $3.49 | yes |
| **RunningHub** 24 GB | RH coins per second | 0.2 coins/s → **~0.1–0.3c** | none | ~$0.15–0.40 *(unverified)* | coin price not published |
| **MimicPC** Large (L4/A10) instant | per hour | $1.29/h → **1.1c** | **yes — until stopped** | $1.29 (bargain $0.79) | yes |
| **MimicPC** Ultra (L40S) | per hour | $2.59/h | yes | $2.59 (bargain $1.59) | yes |
| **HF** ZeroGPU (RTX PRO 6000, 48/96 GB) | daily quota | **$0 marginal** | none | **$0.45** at PRO | yes, HF docs |
| **HF** paid T4 / L4 / 96 GB | per hour | $0.40 / $0.80 / $2.75 | yes, while running | same | yes |

Notes that matter more than the table:

* **RunningHub's coin price is not published** and the derived rate is third-party
  (`~40,000 coins per $10` for subscribers, `~18,000` otherwise). At those figures a 24 GB second
  is $0.00005–0.00011, i.e. $0.18–0.40/h. `orbit_engine` currently carries **$0.02/task** as a
  placeholder, which is 70–200× that; it must be replaced from a real invoice
  (`ENGINE_TASK_USD_RUNNINGHUB`) before the ledger means anything.
* **RunPod's per-second rate still pays a cold start per job.** Its 24 GB class is cheap per
  second and expensive per *first* second: ~45 s of weight load dominates a single order, which
  is why `orbit_engine` models RunPod as `cold_probable` and Modal wins single orders while
  RunPod wins bursts.
* **MimicPC bills while idle**, so it is a batch machine. Its own page says all orders are
  non-refundable.
* **HF PRO is the cheapest GPU time in the list** — $9 for 40 min/day ≈ 20 h/month on a 96 GB
  Blackwell, $0.45/h — but it is a shared queue with a hard daily ceiling. It is capacity for QA
  and demo traffic, not a paid render rail.

---

## 3. Two products, and where each may ship

| | **PUBLIC** — JINXAI ORBITAL | **PRIVATE** — ORBITAL CORE |
|---|---|---|
| Core | stock Krea 2 Turbo | uncensored merge + abliterated encoder |
| Adapters | 0–2 SFW, or none | her adapter + house pair |
| Modal | yes (signature frames) | yes (only host with fit/4K/video) |
| RunPod | yes | **yes** — the adult-capable cheap GPU |
| RunningHub | yes, SFW only | **no** — platform filters NSFW |
| MimicPC | batch only | **no** — sublicensable UGC licence |
| Hugging Face | yes (front desk, ZeroGPU) | **no** — NCII/consent policy |
| Sells | subscriptions, credits, QA | commissions, adapters, subscriptions |

Two legal gates worth writing down before anything is published:

* **MimicPC's terms** grant the platform a non-exclusive, royalty-free, worldwide,
  **sublicensable and transferable** licence over user content. For a licensed performer's
  identity that is a licence you cannot grant on her behalf. Use MimicPC for SFW batch work only.
* **The Krea 2 Community License** is permissive but requires an enterprise licence above 50
  seats. If "seats" reaches end users of a public Space or app, a successful launch crosses that
  line; get the vendor's reading in writing before scaling the public side, and keep the private
  deck on the licence you already hold.

---

## 4. Four profit lines

| Line | What it is | Marginal cost | Margin |
|---|---|---|---|
| **A. Render arbitrage** | route commodity frames to the cheapest permitted host | 0.1–1.1c vs 3.7c | 3–25× on GPU cost |
| **B. QA-certified delivery** | score → retry → certify → audit record | ~1c per judge pass | 83% on the fee, and it protects the price |
| **C. Credit float + BYOK** | prepaid credits, rollover, brought-own-key | 0 until rendered; ~1c/frame on BYOK | 61–66% normal, **92–93% BYOK** |
| **D. Platform credit** | ZeroGPU quota, free coins, referrals, first-month plans | negative (it is a cost offset) | not revenue — booked separately |

Line D is deliberately not counted as income. A free ZeroGPU minute is a dollar of compute you
did not spend, not a dollar you earned; booking it as revenue is how a plan ends up "profitable"
while the bank account disagrees.

---

## 5. QA as the profit engine

### 5.1 What QA actually changes

The counter-intuitive part, and the reason `orbit_qa.cost_per_accepted` ignores the retry budget:

> **Retries do not make accepted work cheaper.** Expected spend and expected acceptance rise
> together, so `E[cost] / P(accept) = cost / p` for any budget. A 5c frame accepted 62% of the
> time costs **8c per accepted frame** whether you allow one attempt or three.

What retries change is **revenue**. At 62% single-shot acceptance, 38% of orders are refunds —
you spend the GPU and book nothing. Three attempts move delivery probability to 94.5%. On a $1
order:

| | Revenue | GPU cost | Margin |
|---|---|---|---|
| Single shot | 62c | 5c | **57c** |
| Up to 3 attempts | 95c | 8c | **87c** |

**+30c per order, for +3c of GPU.** That is the entire QA business in one row. Scaled to 1,000
orders a month it is ~$300 of contribution and ~$15 of judge passes.

### 5.2 QA must be sold, not bundled

At full use, a 2-attempt promise costs 1.52× the frames. Bundled into the existing tiers that
breaks the margin floor:

| Plan | Margin now | Margin with QA bundled | Verdict |
|---|---|---|---|
| COMET $29 | 65.5% | **47.4%** | below 55% — no |
| PULSAR $99 | 61.8% | **41.8%** | below 55% — no |
| COMET + Certified at 6c/frame | — | **62.8%** | yes |
| PULSAR + Certified at 6c/frame | — | **59.1%** | yes |

So `Certified` is an **add-on priced per frame**, not a feature given away with the tier. That is
also better for the customer: they can see what the promise costs and decide.

### 5.3 The tiers

| Tier | Bar | Attempts | Fee | Judge cost | What the customer gets |
|---|---|---|---|---|---|
| Deliver | none | 1 | 0 | 0 | the frame as rendered |
| **Certified** | 7.5 / 10 | 2 | 6c | ~1c | scored before delivery, one re-seed, score attached |
| **Studio** | 8.5 / 10 | 3 | 14c | ~2c | three attempts, stored sha256 audit record, replace or refund |

The defect taxonomy maps each failure to the *cheapest* action that clears it, which is what
keeps a retry at one frame instead of a rewrite:

| Defect | Action | Why |
|---|---|---|
| `content` | **refuse** | age / consent / real person — never delivered, never retried |
| `anatomy` | reseat | a new seed clears hands more often than a new prompt |
| `face` | adapter | identity drift: her adapter must outrank the house pair |
| `detail` | upscale | 4K refine is cheaper than re-rolling a good composition |
| `composition` / `texture` | reprompt | the subject or the quality clause is wrong, not the render |
| `artifact` | reseat | tiling joins and banding are seed-dependent |

### 5.4 QA is also a product you can sell to other operators

The judge rides the encoder that is already resident, so marginal cost is ~1c and the fee is
6–14c. `orbit_qa.sell()` prices it for **any** ComfyUI operator, including ones whose GPUs are
not ours. Two packaged forms:

* **Certification API** — post a frame, get `{score, defects, verdict, sha256}`. Sold per call.
* **Escrow release** — for the Second Life escort market, the L$ leaves the client's wallet only
  on `verdict == accept`, with the audit record as the receipt. Charge 2–3% of order value; the
  GPU cost is already paid by the render line. This is the only line here that scales with *order
  value* rather than with compute.

---

## 6. The membership / credit system

One unit the customer can reason about: **1 credit = 1 accepted 1 MP frame**. Adapter fit = 3,
reel = 2, 4K = 4. Everything is integer credits and integer cents (`orbit_credits.py`).

| Plan | Price | Credits | Cost at full use | Margin | Per credit | Annual (10×) | BYOK margin |
|---|---|---|---|---|---|---|---|
| Guest | $0 | 2/day | $0 | — | — | — | — |
| **COMET** | $29 | 200 | $10.00 | **65.5%** | 14.5c | $290 → 58.6% | **93.1%** |
| **PULSAR** | $99 | 790 | $37.80 | **61.8%** | 12.5c | $990 → **54.2%** | **92.5%** |
| **QUASAR** | $399 | 2,860 | $136.20 | **65.9%** | 14.0c | $3,990 → 59.0% | **93.3%** |

**The annual finding:** "two months free" is charged as 10 months and delivers 12 allowances, so
its cost is twelve months of compute. COMET and QUASAR still clear 55%; **PULSAR at 10× lands at
54.2%** and does not. Fix by pricing annual at **10.5× ($1,039)** → 56.3%, or by gating annual to
plans whose full-use margin is above 63%. This is exactly the kind of number that is invisible
until it is calculated, and it is why `unit(annual=True)` exists.

### 6.1 Why it is superprofitable

1. **The float.** Cash arrives on day one; cost arrives only when the customer renders. A member
   who renders 60% of their allowance is worth 40% breakage — the plan's best-case margin is its
   *unused* margin, and nothing in the price list depends on them using it.
2. **BYOK is 92–93% margin.** A member who supplies a RunningHub or RunPod key pays us for the
   prompt, her adapter, the quality gate and the delivery record. Their key pays the GPU. We
   charge the same price and stretch their credits **3×**, so it is cheaper for them and far more
   profitable for us. This is the single best line in the sheet.
3. **Referrals are paid in credits, not cash.** 20% of a referee's first month is ~136 credits on
   PULSAR: worth $19.80 to them, **$6.80 to us**. CAC is paid in a currency we mint.
4. **Rollover is a bounded liability.** Unused included credits roll over for 1/2/3 months,
   capped at one month's allowance, and expire. Free plans roll nothing over.
5. **Top-ups never undercut the floor.** 100 credits $14 (64.3%), 500 $60 (58.3%), 2,000 $230
   (56.5%). The bigger the pack, the thinner the margin, and none is below 55%.

### 6.2 Spend order (customer-favouring on purpose)

Included → rollover → bought credits. The customer's own money is consumed last, which is what
makes a top-up feel safe and a rollover feel earned. Bought credits never expire; rolled-over
credits do.

### 6.3 Benefit packing at zero marginal cost

These cost nothing to grant and are what separates a $99 plan from 700 commodity frames:

* **Certified by default** on PULSAR/QUASAR (fee waived) — the QA is our cost, so bundling it
  into the top tiers is affordable where it is not on COMET.
* **Off-peak bonus** — +25% credits for scheduling outside the busy window. It costs us less to
  serve and pays the customer in a currency we mint, not in cash we refund.
* **Her adapter, fitted** — the per-image fit is the one thing only this pipeline does; it is
  already paid for by the render line.
* **Commercial licence**, private gallery, adapter slots (1/3/10), API key on QUASAR.
* **Delivery record** — the sha256 audit per frame; a dispute is settled by hashing the file the
  customer holds.
* **Rollover** — the benefit customers actually name when asked why they stay.

---

## 7. Earning and saving platform credit

This is the part that makes a free tier possible without a marketing budget. All figures dated
November 2026; `verified` means read off the vendor's own page.

| Programme | Cash | What it buys | Effective | Verified |
|---|---|---|---|---|
| **HF free** ZeroGPU | $0 | 5 min/day ≈ 150 min/mo on a 96 GB card | $0 | yes |
| **HF PRO** ZeroGPU | $9/mo | 40 min/day ≈ **20 h/mo**; overage $1/10 min | **$0.45/h** | yes |
| **RunningHub** daily | $0 | 100 RH coins/day ≈ 8 min on 24 GB | $0 | yes |
| **RunPod** referral | $0 | 5% serverless / 3% pod for 6 mo; $5–$500 at $10 spend; 10% at affiliate (25 referrals) | — | yes |
| **RunningHub** refer | $0 | 500 RH coins to both sides | — | yes |
| **MimicPC** first month | $6.98 | $12 GPU credit | **172% of cash** | yes |
| **MimicPC** Bargain vs Instant | — | $0.79 vs $1.29/h on a 24 GB card | 39% saving | yes |
| **RunPod** Community vs Secure (4090) | — | $0.34 vs $0.69/h | 51% saving | yes |

What to do with each:

* **HF PRO $9 is the cheapest QA capacity you will ever buy.** Put the judge on ZeroGPU
  (`@spaces.GPU`), keep the paid rail's warm encoder for when the queue is slow. 20 GPU-hours a
  month covers ~72,000 judge passes at 1 s each.
* **HF free is the demo rail.** 150 min/month is ~200 frames — enough for a public "try it" that
  costs nothing, provided the Space points at the **SFW** route (§1).
* **RunPod referral is a distribution channel, not a discount.** Every operator you bring to
  RunPod pays you 5% of their serverless spend for six months — which is also the argument for
  publishing the deployment, not just using it.
* **MimicPC's first month is a pure arbitrage** — $12 of credit for $6.98, non-refundable, so
  spend it in a batch and cancel before renewal. Do not build a workflow around it.
* **Bargain/Community tiers are for batch, never for a storefront.** They are interruptible; a
  client waiting on a frame is not.

---

## 8. Activation order and parity gaps

Do these in this order — each one is a separate bill, and the routing layer already refuses a
host that would render at a loss (`ENGINE_MIN_MARGIN_BPS`, default 55%).

| # | Host | Enable with | Then verify | Blocks |
|---|---|---|---|---|
| 1 | RunPod | `RUNPOD_API_KEY`, `RUNPOD_ENDPOINT_ID` | one order, `GET /api/engine` shows it enabled and cheaper | `deploy/runpod/handler.py` exists; no adapter/video support |
| 2 | RunningHub | `RUNNINGHUB_API_KEY`, `RUNNINGHUB_WORKFLOW_ID` | `submit.py` prints a PNG, then a paid order | node ids must come from **your** export, not `comfy/_ref` |
| 3 | HF Space | `RUNPOD_ENDPOINT_ID` + `RUNPOD_API_KEY` as Space secrets | Space renders a frame on the public worker | **code done** — needs an HF write token to publish |
| 4 | MimicPC | `MIMICPC_COMFY_URL` + `COMFY_WORKFLOW` (tunnel) | `system_stats` answers, batch renders, **stop the app** | no auth on ComfyUI; hourly meter |
| 5 | Modal | always on | keeps fit / 4K / video / adapters | nothing — it is the feature host |

Known gaps, stated plainly:

* **No host but Modal can fit an adapter, tile a 4K refine, or render Wan video.** Those stay on
  Modal by capability, not by preference — and they are what justify the price.
* **The HF Space is a proxy, not an engine.** It renders nothing; it must not be counted as a
  render host (`orbit_engine.RENDER_HOSTS` already excludes it).
* **RunningHub, RunPod and MimicPC rates in `orbit_engine` are placeholders** and each is flagged
  `verified: False`. Replace them from invoices before trusting `/api/engine`.
* **No host implements the locked render contract.** The studio's hard-set variant/encoder/LoRA
  behaviour lives in `app.py`; a remote ComfyUI graph can only be trusted with the SFW public
  product unless the graph itself is pinned.
* **QA and credits are not wired into the API yet.** `orbit_qa` and `orbit_credits` are pure
  modules with offline checks; the routes (`/api/qa/policy`, `/api/credits/plans`) and the
  entitlement webhook are the next step, and each needs one deploy per `AGENTS.md`.

---

## 9. Predictions

Falsifiable, dated, and cheap to check. Confidences are my own.

| # | Prediction | By | Confidence |
|---|---|---|---|
| 1 | A configured RunPod 24 GB worker undercuts Modal by >3× per frame, but **single** orders still route to Modal because of cold start. | 30 days after RunPod is enabled | 70% |
| 2 | HF PRO's 20 Blackwell-hours/month covers >95% of QA judge passes at <$9/month. | 30 days | 65% |
| 3 | RunningHub's real rate is $0.10–0.40/GPU-hour and its `$0.02/task` placeholder is off by >50×. | first invoice | 50% |
| 4 | Certified attaches to ≥25% of paid frames at 6c when offered as an add-on. | 60 days | 55% |
| 5 | BYOK reaches ≥20% of paying members and lifts blended margin ≥5 points. | 90 days | 50% |
| 6 | Annual mix ≥30% costs ~2 points of monthly margin and buys ~3 months of runway in cash. | 90 days | 60% |
| 7 | The adult deck cannot stay on HF or RunningHub; attempting it ends in removal, not a warning. | 30 days | 85% |
| 8 | MimicPC's UGC sublicence makes it unusable for a licensed performer's identity; only SFW batch survives review. | before first adult batch | 80% |

---

## 10. Kill criteria

| Signal | Threshold | Action |
|---|---|---|
| Certified attach rate | <10% at 60 days | bundle QA into COMET and raise it to $35 |
| HF judge p50 latency / queue wait | >8 s / >60 s | judge on the warm Modal worker instead |
| BYOK share | <5% at 90 days | drop the multiplier from 3× to 2× and re-price |
| RunPod cold-start share | >60% of orders | keep RunPod as overflow only, not primary |
| Annual PULSAR margin | stays <55% | price annual at 10.5× or exclude PULSAR |
| Any host's rate unverified | at invoice time | set `ENGINE_RATE_*` / `ENGINE_TASK_USD_*` before trusting `/api/engine` |

---

## 11. What is not built yet

1. **Entitlement.** No payment webhook consumes `payment_intent.succeeded`, so nothing unlocks a
   credit balance. Stripe remains unconfigured and `/api/shop/checkout` still 503s by design.
2. **Credit ledger.** `orbit_credits` computes; nothing stores a balance in a Modal Dict yet.
3. **QA routes.** The judge exists (`POST /api/vision/judge`); the policy, retry loop and audit
   write do not.
4. **SFW registry.** The public line has no SFW adapter picks and no SFW showcase frames
   (`launch/README.md` §"What is genuinely missing").
5. **Per-host parity.** No adapter fit, 4K refine or video on any remote host; no automated
   per-host deploy.
6. **Moderation and age gate.** Still required before a public gallery accepts arbitrary prompts.

---

## 12. Sources

* Modal H100 $0.001097/GPU-second — this deployment's invoices, Nov 2026.
* RunPod serverless and pod rates, referral/affiliate terms — `runpod.io/pricing`,
  `docs.runpod.io/accounts-billing/referrals`, Nov 2026.
* RunningHub RH coins (0.2/s on 24 GB), 100 free coins/day, plan band $71.9–$236.9/yr — platform
  pages plus third-party summaries dated 2026-10-17; coin→USD **unverified**.
* MimicPC hourly tiers, $13.95/$12 and $26.95/$25 plans, first month $6.98 — `mimicpc.com/pricing`,
  Nov 2026; UGC licence from `mimicpc.com/terms-of-service` §3.3.
* Hugging Face ZeroGPU quotas (2 / 5 / 40 / 60 min per day), overage $1 per 10 min, Spaces
  hardware, paid-plan requirement for compute Spaces — `huggingface.co/docs/hub/spaces-zerogpu`,
  `spaces-overview`, `pricing`, Nov 2026.
* Hugging Face content policy on NCII and consent, and the 2026 AI Forensics audit — 
  `huggingface.co/content-policy`, reporting dated July 2026.
* Krea 2 Community License, >50 seats — `krea.ai/krea-2-licensing` via `README.md`.
