"""Credits, plans, and the float that makes a membership profitable.

A subscription is not a discount. It is a prepaid balance with an expiry, and that is the
whole economic trick: cash arrives on day one, the cost arrives only when the customer
renders, and whatever they never render is breakage. This module makes that mechanical
instead of aspirational — integer credits, one spend order, a bounded rollover, and a cost
booked against the same plan the customer paid for.

It also prices the thing that actually makes the margin: **compute we do not pay for**. A
member who brings their own RunningHub or RunPod key still needs the prompt, her adapter, the
quality gate and the delivery record — we charge for those and their credits pay the GPU. The
same is true of a ZeroGPU quota or a host's free daily coins: they are a *cost offset*, never
revenue, and they are booked in their own table so nobody mistakes them for a sale.

What this refuses to do:

  * price a plan below `MIN_MARGIN_BPS` at full use — the table is generated, not negotiated,
  * turn rollover into unbounded liability (it is capped and it expires), or
  * count a platform's free credit as income.

Pure Python, integer credits and integer cents: testable without a GPU, a deploy or a key.
"""

from __future__ import annotations

import time

MIN_MARGIN_BPS = 5500

# One unit of the catalogue in credits, and what it costs us to make. Credits are the price
# unit the customer sees; cents are the truth. `still4k` is a refine pass over an existing
# frame (115 s measured) rather than a new one.
COSTS = {"frame": 1, "imprint": 3, "reel": 2, "still4k": 4}
UNIT_CENTS = {"frame": 5, "imprint": 10, "reel": 6, "still4k": 13}

# What a brought-own-key order costs us: the quality gate and the orchestration, not the GPU.
BYOK_UNIT_CENTS = 1

# Plans keep the names the page already sells (`/aigf#plans`) so nothing on the rail breaks.
# `rollover_months` bounds the liability; `benefits` is what the customer is actually buying,
# because a credit count alone is a commodity and a credit count plus a promise is not.
PLANS: dict[str, dict] = {
    "guest": {
        "label": "Guest",
        "price_cents": 0,
        "included": {},
        "daily": {"frame": 2},
        "rollover_months": 0,
        "benefits": {"rail": "free", "qa": "deliver", "byok": False,
                     "commercial": False, "support": "none"},
        "line": "Two frames a day on the house rail, watermarked, no queue priority.",
    },
    "comet": {
        "label": "COMET",
        "price_cents": 2900,
        "included": {"frame": 200},
        "rollover_months": 1,
        "benefits": {"qa": "certified", "byok": True, "commercial": True,
                     "adapters": 1, "support": "email", "gallery": "private"},
        "line": "200 frames, her adapter fitted, every frame scored before delivery.",
    },
    "pulsar": {
        "label": "PULSAR",
        "price_cents": 9900,
        "included": {"frame": 700, "imprint": 10, "reel": 30},
        "rollover_months": 2,
        "benefits": {"qa": "certified", "byok": True, "commercial": True,
                     "adapters": 3, "support": "priority", "gallery": "private",
                     "offpeak": True},
        "line": "700 frames, 10 adapter fits, 30 reels, two months of rollover.",
    },
    "quasar": {
        "label": "QUASAR",
        "price_cents": 39900,
        "included": {"frame": 2500, "imprint": 40, "reel": 120},
        "rollover_months": 3,
        "benefits": {"qa": "studio", "byok": True, "commercial": True,
                     "adapters": 10, "support": "dedicated", "gallery": "private",
                     "offpeak": True, "api": True},
        "line": "2,500 frames, 40 fits, 120 reels, the 8.5 delivery bar and an API key.",
    },
}

# Annual is ten months paid for twelve delivered: two free months, and a year of cash up front.
# It is the best cash-flow instrument in the list, which is why it is not advertised as a
# discount. `ANNUAL_PERIODS` matters: the customer receives twelve allowances, so the cost is
# twelve months of compute, not one — the table checks that this still clears the floor.
ANNUAL_MONTHS = 10
ANNUAL_PERIODS = 12

# Top-ups exist so a heavy month does not become a refund or a churn. The per-credit price
# falls with size but never below the margin floor at full use.
TOPUPS: dict[int, int] = {100: 1400, 500: 6000, 2000: 23000}

# A member who supplies their own GPU key pays us for the product around the render, so their
# credits stretch three times as far. Our cost drops to the gate, and the margin goes up, not
# down — this is the most profitable line in the sheet and it costs the customer less.
BYOK_MULTIPLIER = 3

# Off-peak rendering costs less to serve, so it pays the customer in credits rather than in a
# discount that would have to be given back at renewal.
OFFPEAK_BONUS_BPS = 2500

# A referral is paid in credits, not cash: it costs us the compute of one month at the margin
# we already accept, and it converts only when the referee actually renders.
REFERRAL_BPS = 2000

# Rollover is capped at one month of the plan's own included credits per rollover month, so a
# quiet customer cannot bank a year of renders and demand them after a price change.
ROLLOVER_CAP_BPS = 10000

MONTH = 30 * 24 * 3600


def included_credits(plan_id: str) -> int:
    spec = PLANS.get(str(plan_id)) or {}
    return sum(int(COSTS.get(kind, 0)) * int(count or 0)
               for kind, count in (spec.get("included") or {}).items())


def included_cost_cents(plan_id: str, byok: bool = False) -> int:
    """What the plan's own allowance costs us at full use."""
    spec = PLANS.get(str(plan_id)) or {}
    table = {k: BYOK_UNIT_CENTS for k in UNIT_CENTS} if byok else UNIT_CENTS
    return sum(int(table.get(kind, 0)) * int(count or 0)
               for kind, count in (spec.get("included") or {}).items())


def price_cents(plan_id: str, annual: bool = False) -> int:
    spec = PLANS.get(str(plan_id)) or {}
    monthly = max(0, int(spec.get("price_cents") or 0))
    return monthly * ANNUAL_MONTHS if annual else monthly


def unit(plan_id: str, annual: bool = False, byok: bool = False) -> dict:
    """One plan's economics at full use: price, cost, margin, and per-credit price.

    `byok` is not a hypothetical: a member with a RunningHub key is served for the gate only,
    and the same $99 plan then clears ~92% instead of ~61%.

    An annual plan is charged ten months and delivers `ANNUAL_PERIODS` allowances, so its cost
    is twelve months of compute. That is exactly the check worth making: "two months free" is
    affordable on COMET and QUASAR and, on a fully-used PULSAR, is not.
    """
    periods = ANNUAL_PERIODS if annual else 1
    credits = included_credits(plan_id) * periods
    price = price_cents(plan_id, annual)
    cost = included_cost_cents(plan_id, byok) * periods
    margin = price - cost
    bps = (margin * 10000 // price) if price else 0
    return {
        "plan": str(plan_id),
        "label": (PLANS.get(plan_id) or {}).get("label", plan_id),
        "annual": bool(annual),
        "byok": bool(byok),
        "priceCents": price,
        "credits": credits,
        "costCents": cost,
        "marginCents": margin,
        "marginBps": bps,
        "perCreditCents": round(price / credits, 2) if credits else 0,
        "clearsFloor": bps >= MIN_MARGIN_BPS,
    }


def topup(credits: int) -> dict:
    """A top-up pack, priced per credit, with the floor checked rather than assumed."""
    want = max(0, int(credits))
    pack = TOPUPS.get(want)
    if pack is None:
        # Unknown size: price it at the nearest published rate, never at zero.
        size = min(TOPUPS) if want < min(TOPUPS) else max(TOPUPS)
        pack = int(round(TOPUPS[size] * want / size))
    cost = want * UNIT_CENTS["frame"]
    margin = pack - cost
    return {
        "credits": want,
        "priceCents": pack,
        "costCents": cost,
        "marginCents": margin,
        "marginBps": (margin * 10000 // pack) if pack else 0,
        "perCreditCents": round(pack / want, 2) if want else 0,
        "clearsFloor": bool(pack) and (margin * 10000 // pack) >= MIN_MARGIN_BPS,
    }


def grant(plan_id: str, prior: dict | None = None, now: int | None = None) -> dict:
    """A new billing period: fresh included credits, and the rollover the last one earned.

    Unused included credits roll over at `ROLLOVER_CAP_BPS` into a balance that expires with
    the new period. A zero-price plan never rolls anything over — free credits that accumulate
    are a liability with no revenue behind them.
    """
    now = int(now or time.time())
    spec = PLANS.get(str(plan_id)) or {}
    months = int(spec.get("rollover_months") or 0)
    included = included_credits(plan_id)
    previous = dict(prior or {})
    unused = max(0, int(previous.get("included") or 0)) + max(0, int(previous.get("rollover") or 0))
    cap = included * ROLLOVER_CAP_BPS // 10000
    carried = min(unused, cap) if months and included else 0
    return {
        "plan": str(plan_id),
        "included": included,
        "rollover": carried,
        "topup": max(0, int(previous.get("topup") or 0)),   # bought credits do not evaporate
        "expires": now + months * MONTH if carried else 0,
        "periodStart": now,
    }


def expire(balance: dict, now: int | None = None) -> dict:
    """Drop rolled-over credits whose period has ended. Bought credits stay."""
    now = int(now or time.time())
    out = dict(balance or {})
    expires = int(out.get("expires") or 0)
    if expires and now > expires:
        out["rollover"] = 0
        out["expires"] = 0
    return out


def spend(balance: dict, credits: int, now: int | None = None) -> dict:
    """Debit in the one order that protects both sides.

    Included credits first (they expire soonest), then rollover, then bought credits — the
    customer's own money is the last thing consumed, which is what makes a top-up feel safe
    and a rollover feel earned.
    """
    want = max(0, int(credits))
    book = expire(balance, now)
    used = {"included": 0, "rollover": 0, "topup": 0}
    left = want
    for bucket in ("included", "rollover", "topup"):
        if left <= 0:
            break
        have = max(0, int(book.get(bucket) or 0))
        take = min(have, left)
        book[bucket] = have - take
        used[bucket] = take
        left -= take
    return {
        "ok": left == 0,
        "wanted": want,
        "used": used,
        "short": left,
        "balance": book,
    }


def cost_of(usage: dict, byok: bool = False) -> dict:
    """Credits and cents for a basket like `{"frame": 12, "reel": 3}`."""
    table = {k: BYOK_UNIT_CENTS for k in COSTS} if byok else UNIT_CENTS
    credits = 0
    cents = 0
    for kind, count in (usage or {}).items():
        n = max(0, int(count or 0))
        credits += int(COSTS.get(kind, 0)) * n
        cents += int(table.get(kind, 0)) * n
    if byok and credits:
        # Brought-own-key members are charged the platform product, not the compute: their
        # credits go three times as far because our bill for the render is the gate.
        credits = max(1, (credits + BYOK_MULTIPLIER - 1) // BYOK_MULTIPLIER)
    return {"credits": credits, "costCents": cents}


def offpeak(credits: int) -> int:
    """Credits granted for scheduling outside the host's busy window."""
    return int(credits) * (10000 + OFFPEAK_BONUS_BPS) // 10000


def referral(first_month_cents: int, plan_id: str = "comet") -> dict:
    """What a referral pays, in credits, and what that costs us.

    Paid in credits at the plan's own per-credit price, so the reward is worth its face value
    to the customer and only costs us the compute they actually use.
    """
    cents = max(0, int(first_month_cents)) * REFERRAL_BPS // 10000
    per = unit(plan_id)["perCreditCents"] or 1.0
    credits = int(cents / per) if per else 0
    return {"valueCents": cents, "credits": credits,
            "costCents": credits * UNIT_CENTS["frame"]}


def base_charge(plan_id: str) -> dict:
    """What the plan costs per month at zero use — the float, before any GPU is touched."""
    price = price_cents(plan_id)
    return {"priceCents": price, "costCents": 0, "marginCents": price,
            "marginBps": 10000 if price else 0}


# --------------------------------------------------------------------------------------
# Credit earned or saved on other hosts
# --------------------------------------------------------------------------------------
#
# These are not our products and they are not revenue. They are the cost side of the business
# on machines we do not own, and they are the reason a free tier can exist at all: 150 GPU
# minutes a month on a 96 GB card costs nothing, and 100 RH coins a day is a render a day.
#
# `verified` means the figure was read off the vendor's own page (date in `source`); an
# unverified figure is somebody's summary and must not be planned against.
PLATFORM_CREDITS: dict[str, dict] = {
    "huggingface_free": {
        "label": "HF free account · ZeroGPU",
        "cashCents": 0,
        "minutes_month": 150,          # 5 min/day
        "gpu": "RTX PRO 6000 Blackwell (48/96 GB)",
        "per_minute_cents_at_overage": 10,
        "verified": True,
        "source": "HF ZeroGPU docs, Nov 2026",
        "gate": "shared queue, 2 Gradio Spaces per account",
    },
    "huggingface_pro": {
        "label": "HF PRO · ZeroGPU quota",
        "cashCents": 900,              # $9/mo
        "minutes_month": 1200,         # 40 min/day
        "gpu": "RTX PRO 6000 Blackwell (48/96 GB)",
        "per_minute_cents_at_overage": 10,
        "verified": True,
        "source": "HF docs/pricing, Nov 2026",
        "gate": "quota resets 24 h after first use; xlarge costs double quota",
    },
    "runninghub_daily": {
        "label": "RunningHub · 100 RH coins/day",
        "cashCents": 0,
        "minutes_month": 250,          # 100 coins / 0.2 coins-per-second ≈ 8.3 min/day
        "gpu": "24 GB class",
        "per_minute_cents_at_overage": 0,
        "verified": True,
        "source": "RunningHub, 2026-10-17 third-party summary",
        "gate": "coins expire in 24 h, one task at a time, 20 min cap",
    },
    "runpod_referral": {
        "label": "RunPod · referral + affiliate",
        "cashCents": 0,
        "minutes_month": 0,
        "gpu": "any",
        "per_minute_cents_at_overage": 0,
        "verified": True,
        "source": "RunPod referral docs, Nov 2026",
        "gate": "5% serverless / 3% pod for 6 months; $5–$500 at $10 spend; 10% at affiliate",
    },
    "mimicpc_first_month": {
        "label": "MimicPC Essential · first month",
        "cashCents": 698,
        "minutes_month": 0,            # credit, not minutes: $12 of GPU time
        "gpu": "T4 / L4 / L40S",
        "per_minute_cents_at_overage": 0,
        "verified": True,
        "source": "MimicPC pricing, Nov 2026",
        "gate": "$12 credit for $6.98 — 172% of cash; renews at $13.95 for $12",
    },
}


def effective_hourly(cash_cents: int, minutes: int) -> float:
    """What a credit programme really costs per GPU-hour, in dollars."""
    hours = max(1e-9, float(minutes) / 60.0)
    return round((cash_cents / 100.0) / hours, 4)


def earn_table() -> list[dict]:
    """The credit programmes, priced per GPU-hour so they can be compared with a real bill."""
    rows = []
    for pid, spec in PLATFORM_CREDITS.items():
        minutes = int(spec.get("minutes_month") or 0)
        rows.append({
            "id": pid,
            "label": spec.get("label"),
            "cashCents": int(spec.get("cashCents") or 0),
            "minutesMonth": minutes,
            "gpu": spec.get("gpu"),
            "effectiveHourly": effective_hourly(int(spec.get("cashCents") or 0), minutes)
            if minutes else 0.0,
            "verified": bool(spec.get("verified")),
            "source": spec.get("source"),
            "gate": spec.get("gate"),
        })
    rows.sort(key=lambda r: (r["effectiveHourly"], -r["minutesMonth"]))
    return rows


def design() -> dict:
    """The whole sheet, generated: every plan, every pack, every credit programme.

    This is what a page or an API should read instead of hard-coding prices, so a change to
    `PLANS` cannot leave a stale number on the storefront.
    """
    plans = []
    for pid in PLANS:
        row = unit(pid)
        row["annualRow"] = unit(pid, annual=True)
        row["byokRow"] = unit(pid, byok=True)
        row["benefits"] = dict((PLANS[pid].get("benefits") or {}))
        row["line"] = PLANS[pid].get("line")
        row["rolloverMonths"] = int(PLANS[pid].get("rollover_months") or 0)
        plans.append(row)
    return {
        "plans": plans,
        "topups": [topup(size) for size in sorted(TOPUPS)],
        "byokMultiplier": BYOK_MULTIPLIER,
        "offpeakBonusBps": OFFPEAK_BONUS_BPS,
        "referralBps": REFERRAL_BPS,
        "annualMonths": ANNUAL_MONTHS,
        "minMarginBps": MIN_MARGIN_BPS,
        "platform": earn_table(),
    }
