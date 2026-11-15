"""Quality assurance as a profit centre, not a cost of doing business.

Three numbers decide whether a render business makes money, and none of them is the GPU
rate:

  * how often a delivered frame is **rejected** — a refund, a re-render, or a client who
    does not come back,
  * what a **retry** costs before delivery rather than after it, and
  * how many retries a **price** can absorb before the order stops clearing its floor.

`orbit_engine` answers "who draws it". This module answers "was it good enough to charge
for", and turns that answer into money: the expected cost of one *accepted* frame, the
margin a QA promise leaves behind, and the highest defect rate a price can support.

The judgement itself already exists on the rail: `POST /api/vision/judge` asks the resident
Qwen3-VL for a blunt verdict. What was missing was the decision layer — a score bound to a
delivery rule, a retry budget, and an audit record that makes a delivered frame provable.

Two things this module is deliberately strict about:

1. **A retry is cheaper than a refund, but not free.** Refunding a $29 order costs the whole
   price; re-seeding costs one attempt. QA is profitable whenever it moves spend from the
   first column to the second, which is why the maths below is in cents and not in opinion.
2. **A promise needs a floor.** If even one attempt busts `MIN_MARGIN_BPS`, no retry policy
   can save that order — the price is wrong, not the model, and `gate` says so instead of
   quietly delivering at a loss.

Pure Python, integer cents: importable and testable without a GPU, a deploy or a model.
"""

from __future__ import annotations

import hashlib
import time

# A frame at or above this score is charged for and delivered. Below `REJECT_SCORE` a frame
# is not worth a second attempt unless a defect says a cheap fix exists.
ACCEPT_SCORE = 7.5
REJECT_SCORE = 5.0

# The same floor `orbit_engine` uses for routing. A QA policy that cannot clear it cannot
# make an order profitable, so it must not be sold as if it could.
MIN_MARGIN_BPS = 5500

# Hard ceiling on attempts per order. Three is the number a $99 subscription can absorb on a
# measured 4.9c frame; more than that and the retry loop becomes the product.
MAX_ATTEMPTS = 3

# Measured blended cost of one attempt, in cents, for the four things this deck sells.
# (80% warm / 20% cold on H100, `launch/economics.md`.) Kept here so a QA decision can be
# priced without importing the engine table.
UNIT_CENTS = {"frame": 5, "imprint": 10, "reel": 6, "still4k": 13}

# What a defect means for the next attempt. `fix` is the cheapest action that plausibly
# clears it; `refuse` is a policy failure that must not be retried or charged.
DEFECTS: dict[str, dict] = {
    "content": {
        "fix": "refuse",
        "why": "the frame breaks the render rule (age, consent, a real person) — never "
               "delivered, never retried",
    },
    "anatomy": {
        "fix": "reseat",
        "why": "hands, fingers, limbs: a new seed clears this more often than a new prompt, "
               "and a seed is one attempt instead of a rewrite",
    },
    "face": {
        "fix": "adapter",
        "why": "identity drift — her adapter must outrank the house pair, or she is not in "
               "the frame",
    },
    "detail": {
        "fix": "upscale",
        "why": "soft or low-detail: a 4K refine pass is cheaper than re-rolling a frame that "
               "already has the right composition",
    },
    "composition": {
        "fix": "reprompt",
        "why": "the subject is wrong, not the render — the prompt needs the fix",
    },
    "texture": {
        "fix": "reprompt",
        "why": "plastic skin, waxy light: the quality clause was too weak",
    },
    "artifact": {
        "fix": "reseat",
        "why": "tiling joins, banding, compression: a new seed, and 4K only after it clears",
    },
}

# Ordered by what they cost us: a reseed is one frame, an adapter change re-renders with her
# weights, an upscale is a refine pass over the same frame.
ACTION_ORDER = ("reseat", "reprompt", "adapter", "upscale", "refuse")

# QA sold as a product. `fee_cents` is what we charge on top of the render for each tier; the
# judge itself runs on the encoder that is already resident, so its marginal cost is ~0 on a
# warm worker and ~1c on a cold one.
TIERS: dict[str, dict] = {
    "deliver": {
        "label": "Deliver",
        "accept": 0.0,
        "attempts": 1,
        "fee_cents": 0,
        "audit": False,
        "line": "The frame is delivered as rendered. No score, no retry, no promise.",
    },
    "certified": {
        "label": "Certified",
        "accept": ACCEPT_SCORE,
        "attempts": 2,
        "fee_cents": 6,
        "audit": True,
        "line": "Scored before delivery; below the bar it is re-seeded once, and the frame "
                "you receive carries its score.",
    },
    "studio": {
        "label": "Studio",
        "accept": 8.5,
        "attempts": MAX_ATTEMPTS,
        "fee_cents": 14,
        "audit": True,
        "line": "Highest bar: up to three attempts, a stored audit record per delivered "
                "frame, and a replacement or refund if nothing clears 8.5.",
    },
}

# The judge is one VLM pass over the frame. Billed at ~1c so a cold worker is never sold QA
# at a loss; on a warm worker this is an overestimate, which is the safe direction.
JUDGE_CENTS = 1


def parse_score(text: str) -> float | None:
    """The score in a judge's prose: `8/10`, `8 out of 10`, `Score: 7.5/10`.

    Returns None when the model did not answer with a number — that is a `review`, not an
    accept, because a missing score is the one case where guessing is most expensive.
    """
    body = str(text or "")
    for token in body.replace(",", " ").split():
        if "/" in token:
            left = token.split("/", 1)[0].strip(".()[]")
            try:
                value = float(left)
            except ValueError:
                continue
            if 0.0 <= value <= 10.0:
                return value
    lowered = body.lower()
    marker = lowered.find("out of 10")
    if marker > 0:
        head = lowered[:marker].strip().rstrip(":. ").split()
        if head:
            try:
                value = float(head[-1])
                if 0.0 <= value <= 10.0:
                    return value
            except ValueError:
                pass
    return None


def fix_for(defect: str) -> dict:
    """The cheapest action that plausibly clears a defect, and why it is that one."""
    spec = DEFECTS.get(str(defect or "").lower()) or {}
    action = spec.get("fix") or "reprompt"
    return {"action": action, "why": spec.get("why") or "no known fix — reprompt"}


def plan_for(defects) -> list[dict]:
    """The actions for a set of defects, cheapest first and each action only once."""
    seen: set[str] = set()
    rows: list[dict] = []
    for defect in defects or []:
        got = fix_for(defect)
        action = got["action"]
        if action in seen:
            continue
        seen.add(action)
        rows.append({"defect": str(defect).lower(), **got,
                     "rank": ACTION_ORDER.index(action) if action in ACTION_ORDER else 99})
    rows.sort(key=lambda row: row["rank"])
    return rows


def verdict(score: float | None, defects=(), accept: float = ACCEPT_SCORE,
            reject_line: float = REJECT_SCORE) -> str:
    """`accept` | `retry` | `reject` | `refuse` | `review` for one judged frame.

    A policy defect outranks a high score on purpose: a beautiful frame that breaks the
    render rule is not a product, it is a liability, and it is never charged for.
    """
    defects = [str(d).lower() for d in (defects or [])]
    if any(fix_for(d)["action"] == "refuse" for d in defects):
        return "refuse"
    if score is None:
        return "review"
    if score >= accept:
        return "accept"
    fixable = any(fix_for(d)["action"] != "refuse" for d in defects)
    if score <= reject_line and not fixable:
        return "reject"
    return "retry"


def attempts_affordable(price_cents: int, attempt_cost_cents: int,
                        min_margin_bps: int = MIN_MARGIN_BPS,
                        hard_cap: int = MAX_ATTEMPTS) -> int:
    """How many attempts a price can carry before the order falls under its floor.

    Returns 0 when even a single attempt busts the floor: that is a pricing bug, and the
    caller must be told rather than given a retry it cannot afford.
    """
    price = max(0, int(price_cents))
    cost = max(0, int(attempt_cost_cents))
    floor = price * max(0, min(10000, int(min_margin_bps))) // 10000
    room = price - floor
    if cost <= 0:
        return hard_cap if price else 0
    fits = room // cost
    return max(0, min(hard_cap, fits))


def p_within(accept_rate: float, attempts: int) -> float:
    """Probability that at least one of `attempts` independent frames is accepted."""
    p = max(0.0, min(1.0, float(accept_rate or 0.0)))
    n = max(1, int(attempts))
    return 1.0 - (1.0 - p) ** n


def expected_attempts(accept_rate: float, attempts: int) -> float:
    """Expected frames drawn per order: the loop stops on the first acceptance."""
    p = max(0.0, min(1.0, float(accept_rate or 0.0)))
    n = max(1, int(attempts))
    if p <= 0.0:
        return float(n)
    if p >= 1.0:
        return 1.0
    return round((1.0 - (1.0 - p) ** n) / p, 4)


def cost_per_accepted(attempt_cost_cents: int, accept_rate: float,
                      attempts: int = 1) -> int:
    """Cents of GPU per frame a customer actually accepts — the number that decides price.

    A 5c frame that is accepted 62% of the time costs **8c** per accepted frame, which is why
    quoting the render cost as the unit cost is the most common way to lose money.

    `attempts` does not change this: expected spend and expected acceptance rise together, so
    E[cost]/P(accept) = cost/p for any retry budget. Retries do not make accepted work
    cheaper — they make *refunds* rarer, which is a revenue effect, not a cost one. The
    argument is kept so callers can state the budget they mean.
    """
    p = max(0.0, min(1.0, float(accept_rate or 0.0)))
    if p <= 0.0:
        return 10 ** 9
    return int(round(max(0, int(attempt_cost_cents)) / p))


def expected(price_cents: int, attempt_cost_cents: int, accept_rate: float,
             attempts: int = 1) -> dict:
    """Money for one order under an attempt budget, in integer cents.

    On total failure the customer is refunded and no revenue is booked — but the GPU minutes
    were still spent, so the cost stays in the ledger. That asymmetry is the reason a refund
    is more expensive than a retry, and it is modelled here rather than assumed away.
    """
    price = max(0, int(price_cents))
    cost_each = max(0, int(attempt_cost_cents))
    n = max(1, int(attempts))
    p_accept = p_within(accept_rate, n)
    tries = expected_attempts(accept_rate, n)
    cost = int(round(tries * cost_each))
    revenue = int(round(price * p_accept))
    margin = revenue - cost
    return {
        "priceCents": price,
        "attempts": n,
        "acceptRate": round(max(0.0, min(1.0, float(accept_rate or 0.0))), 4),
        "acceptProbability": round(p_accept, 4),
        "expectedAttempts": tries,
        "costCents": cost,
        "revenueCents": revenue,
        "marginCents": margin,
        "marginBps": (margin * 10000 // price) if price else 0,
    }


def compare(price_cents: int, attempt_cost_cents: int, accept_rate: float,
            attempts: int = MAX_ATTEMPTS) -> dict:
    """What the QA promise is worth against a single shot, per order and per 100 orders.

    Positive `deltaCents` is the honest case for QA: it is not a cost, it is the difference
    between refunding a bad frame and spending one more 4.9c attempt on a good one.
    """
    before = expected(price_cents, attempt_cost_cents, accept_rate, 1)
    after = expected(price_cents, attempt_cost_cents, accept_rate, attempts)
    delta = after["marginCents"] - before["marginCents"]
    return {
        "before": before,
        "after": after,
        "deltaCents": delta,
        "deltaPer100": delta * 100,
        "deltaBps": after["marginBps"] - before["marginBps"],
    }


def gate(price_cents: int, attempt_cost_cents: int, accept_rate: float,
         attempts: int = MAX_ATTEMPTS, min_margin_bps: int = MIN_MARGIN_BPS) -> dict:
    """Whether this price can carry this QA promise at all.

    `allow` is false for two different reasons and they are reported separately, because the
    fix is different: a thin price needs a new price, a weak acceptance rate needs a better
    prompt or a better acceptance bar.
    """
    allowed_attempts = attempts_affordable(price_cents, attempt_cost_cents, min_margin_bps,
                                            attempts)
    got = expected(price_cents, attempt_cost_cents, accept_rate,
                   max(1, allowed_attempts) if allowed_attempts else 1)
    if allowed_attempts < 1:
        return {"allow": False, "reason": "price cannot carry even one attempt",
                "attempts": 0, **got}
    if got["marginBps"] < min_margin_bps:
        return {"allow": False, "reason": "expected margin below floor at this accept rate",
                "attempts": allowed_attempts, **got}
    return {"allow": True, "reason": "", "attempts": allowed_attempts, **got}


def sell(tier: str, price_cents: int, attempt_cost_cents: int, accept_rate: float) -> dict:
    """QA sold as a product: the fee, the judge cost and the margin it books.

    This is the line that makes QA a business rather than a policy. It is priced per frame
    and costs one VLM pass, so it can be sold to any ComfyUI operator — including the ones
    whose GPUs are not ours.
    """
    spec = TIERS.get(str(tier)) or TIERS["deliver"]
    fee = int(spec.get("fee_cents") or 0)
    works = expected(price_cents, attempt_cost_cents, accept_rate,
                     int(spec.get("attempts") or 1))
    # One judge pass per attempt actually drawn, because a retry has to be judged too — but a
    # tier that does not judge costs nothing, which is why `deliver` is free rather than a
    # one-cent loss.
    passes = int(round(works["expectedAttempts"])) if spec.get("audit") else 0
    cost = JUDGE_CENTS * max(0, passes)
    margin = fee - cost
    return {
        "tier": str(tier),
        "label": spec.get("label"),
        "feeCents": fee,
        "judgeCents": cost,
        "marginCents": margin,
        "marginBps": (margin * 10000 // fee) if fee else 0,
        "attempts": spec.get("attempts"),
        "audit": bool(spec.get("audit")),
        "deliveryMargin": works,
    }


def audit(image: bytes, score: float | None, defects, engine: str = "",
          order_id: str = "") -> dict:
    """A tamper-evident record for one delivered frame.

    The digest is over the exact bytes the customer received, so a dispute is settled by
    hashing the file they hold rather than by trusting either side's screenshots. This is
    what lets a QA tier be sold as proof instead of as a promise.
    """
    raw = image if isinstance(image, (bytes, bytearray)) else b""
    return {
        "sha256": hashlib.sha256(bytes(raw)).hexdigest() if raw else "",
        "bytes": len(raw),
        "score": score,
        "defects": [str(d).lower() for d in (defects or [])],
        "verdict": verdict(score, defects),
        "engine": str(engine or "")[:40],
        "orderId": str(order_id or "")[:64],
        "at": int(time.time()),
    }


def policy(tier: str = "certified") -> dict:
    """What the API and the page are allowed to say about the QA promise."""
    spec = TIERS.get(str(tier)) or TIERS["certified"]
    return {
        "tier": str(tier),
        "accept": float(spec.get("accept") or 0.0),
        "attempts": int(spec.get("attempts") or 1),
        "feeCents": int(spec.get("fee_cents") or 0),
        "audit": bool(spec.get("audit")),
        "line": spec.get("line"),
        "defects": {name: fix_for(name) for name in DEFECTS},
        "tiers": {name: {"label": s["label"], "accept": s["accept"],
                         "attempts": s["attempts"], "feeCents": s["fee_cents"]}
                  for name, s in TIERS.items()},
    }
