# JINXAI price sheet

Prices come from measured cost, not from competitors. The GPU rate is $0.001097/GPU-second =
**$3.9492 per hour** (verified, November 2026), billed per second.

## Unit cost, before pricing

Measured on this deck:

| Unit | Time | Cost |
|---|---|---|
| Frame, 8 steps, 1024² | 33 s warm / 92 s cold | $0.0362 / $0.1009 |
| Frame + adapter imprint | 77 s warm | $0.0845 |
| Imprint alone | 44 s | $0.0483 |
| Reel, 81 frames | 39 s warm / 97 s cold | $0.0428 / $0.1064 |

Low traffic means most requests pay a cold start, so price against the **blended** figure
(80% warm / 20% cold):

| Unit | Blended cost |
|---|---|
| Frame | **$0.0491** |
| Frame + imprint | **$0.0974** |
| Reel | **$0.0555** |

Payment processing takes 2.9% + $0.30, so net revenue on a $99 charge is $95.83.

## Tiers

Monthly, USD, prepaid. Each cap is priced at 3× blended cost at full use and rounded to a
defensible number; margin grows as utilisation drops, which is the point of a cap.

| | **COMET** | **PULSAR** | **QUASAR** |
|---|---|---|---|
| Price | **$29/mo** | **$99/mo** | **$399/mo** |
| Frames | 200 | 700 | 2,500 |
| Imprints | 0 | 10 | 40 |
| Reels | 0 | 30 | 120 |
| Cost at full use | $9.83 | $37.04 | $133.42 |
| Net after fees | $27.86 | $95.83 | $387.13 |
| **Gross margin** | **64.7%** | **61.3%** | **65.5%** |
| Overage, frame | $0.15 | $0.15 | $0.12 |
| Overage, imprint | — | $0.29 | $0.26 |
| Overage, reel | — | $0.20 | $0.17 |

Annual: **10× monthly** (two months free), same caps, billed once.

Why 3×: contribution survives a month of full utilisation, a refund, and a retry tax. Below
2.5× you lose money the moment a customer uses what they paid for — which is the normal case.

## Break-even

Fixed cash cost, monthly:

| Item | Cost |
|---|---|
| Team platform tier (needed for a branded domain) | $250.00 |
| Domain | ~$1.00 |
| **Total** | **≈ $251.00** |

On the free tier the fixed cost is **$0** and the app answers on a vendor-shaped URL —
profitable from the first COMET subscriber, unbranded address.

Contribution per subscriber, and the count that covers $251:

| Tier | Contribution | Subscribers to break even |
|---|---|---|
| COMET | $18.03 | 14 |
| PULSAR | $58.79 | **5** |
| QUASAR | $253.71 | **1** |

**Five PULSAR subscribers pay for the platform tier.** That is the business case: a small,
specific number of customers covers the fixed line, and each one beyond it is contribution.

## The profitability test

A configuration is profitable only if all four hold:

1. Gross margin ≥ 55% at full included usage.
2. Fixed cost covered by ≤ 10 subscribers of the cheapest tier that includes reels.
3. No tier where one heavy user can exceed 40% of net revenue.
4. Marketplace-app revenue counted as **$0** until a settled statement exists — the point
   rate is not published, so it cannot be forecast.

## Marketplace line (upside only)

The public app runs on the host's GPUs, so marginal cost is zero.

- Subscription: $9.90 list, 70% to the creator, ~10% handling fee deducted first →
  **≈ $6.24 per subscriber per month**, pure contribution.
- Incentive pool: unknown rate, withheld until a real statement arrives.
- Withdrawal threshold: $50 (one page states $100 combined) — assume nothing withdrawable in
  month one.

Treat every marketplace dollar as upside against zero. If it proves real, it is the cheapest
acquisition in the plan.

## What would make this unprofitable

- Removing the single-container cap: a public loop can then reach 10 GPUs ≈ $948/day.
- Selling "unlimited": blended cost is linear, so unlimited is a bet against your customers.
- Cold-start-heavy traffic: above ~50% cold a frame costs $0.10 and COMET loses money.
- Leaving still and motion workers warm together: two GPUs, $7.90/hr, regardless of use.
