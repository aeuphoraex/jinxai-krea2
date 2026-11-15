# Economics

Two different businesses share this engine. Keep them separate when doing the maths.

## 1. What a render costs here (Modal, private pipeline)

Measured on H100, from this project's own runs:

| Operation | Warm | Cold (includes weight load) |
|---|---|---|
| Image, 8 steps, 1024² | 33 s | ~92 s |
| Per-image LoRA fit (rank 8, 40 steps) | 44 s | — |
| Motion reel, 832×832, 81 f, 24 steps | 39 s | 97 s |

At Modal's published **$0.001097/GPU-second = $3.9492 per H100-hour** (pricing page,
November 2026; verified, not assumed):

```
image alone        33 s → $0.036
image + LoRA fit   77 s → $0.085
cold first image   92 s → $0.101
1,000 images       9.2 h → $36 warm, $85 with a LoRA fit each
```

Idle cost is the trap: the image worker stays up 300 s after the last request, the video
worker 180 s and the web tier 600 s. A single late-night image can cost more in idle time than
in compute. Lower `KREA2_SCALEDOWN` for a low-traffic deployment.

The `max_containers=1` cap on the GPU workers is also the abuse ceiling for a public URL: at
most one image H100 plus one video H100, which is $7.90/hr ≈ $190/day if someone loops the
endpoint. Remove the cap and Starter's 10-GPU concurrency allows ~$948/day instead. See
`modal_release.md`.

**Cost floor for selling API access:** $0.085 per image with the fit, $0.036 without.
A 3× floor, which covers review time and failure retries, is **$0.26 per image**, or
**$0.11** if the customer does not want the per-image LoRA.

## 2. What SeaArt pays

Quoted from SeaArt's own pages, November 2026:

- **Creator Incentive Program** covers Original Models, AI Apps and AI Characters. Earnings
  come from *valid generations by real unique users*, accumulated as points and settled to
  cash monthly. Posting alone earns nothing; there is a unique-user threshold before the
  incentive process starts.
- **Withdrawal threshold: $50** per the FAQ (a subscription page states $100 for combined
  subscription + incentive earnings). Treat $50 as the lower bound and confirm in the
  dashboard.
- **Tiered rewards:** up to 15 works per month can earn from the inclusive tier; then a fixed
  prize pool plus revenue sharing with no cap; then viral awards of $1,000 and $2,000.
- **Subscriptions:** uniform $9.90, 70% to the creator, 30% platform, with ~10% handling fee
  deducted before the split. Phase 1 covers AI models and AI apps. Approved creators can get
  a $200–500 monthly incubation fund and per-subscriber bonuses from $3 to $5.
- **Stacking rule:** if several of your own models/LoRAs are used in one creation, it counts
  once. A two-LoRA stack does not double incentive weight.

What SeaArt does **not** publish is a point-to-dollar rate. So there is no honest way to
compute revenue per generation in advance — anyone quoting one is guessing.

## 3. Break-even, stated as a gate

The app costs you nothing per use on SeaArt (their GPUs, their credits), so the SeaArt app
has **zero marginal cost** and any incentive revenue is margin. The only costs are the assets
(LoRAs, showcase images) and your time.

The private pipeline is the inverse: real marginal cost, $0.036–$0.085 per image. To carry it:

| Monthly images | GPU cost | Price at 3× floor | Required |
|---|---|---|---|
| 1,000 | $36–85 | $260 | one small client |
| 10,000 | $360–850 | $2,600 | ~$2.6k MRR |
| 100,000 | $3,600–8,500 | $26,000 | volume pricing, A100 to halve the hourly |

## 4. The strategy that follows

1. **SeaArt app = distribution.** Zero marginal cost, their traffic, incentive + subscriptions.
   Ship the SFW portrait app. It is also the demo that sells the private work.
2. **Private pipeline = margin.** Uncensored stack, prompt expansion, per-image LoRA export,
   Wan video. Sold as API access at 3× floor, or as fixed-scope commissions.
3. **Do not blur them.** NSFW on SeaArt jeopardises the account that carries the distribution;
   SFW-only on the private server throws away what it is actually good at.

## 5. Predictions to check against reality

These are falsifiable, dated, and cheap to verify:

- The public app gets its first 100 valid generations within 30 days of publishing, or the
  listing copy/thumbnail is the problem, not the model. (>50% confidence)
- Incentive earnings from one app stay under $50 for the first two months — below the
  withdrawal threshold — unless the app is featured. (>60%)
- Subscription revenue, not the incentive pool, becomes the larger line by month three, given
  the 70/30 split and the $200–500 incubation fund. (>50%)
- Private API pricing at $0.11/image closes more deals than $0.26 for hobbyist buyers, and
  fewer for studios. (>50%)

Log the actual month-one numbers here when they exist; the point of writing them down is to
find out which one was wrong.
