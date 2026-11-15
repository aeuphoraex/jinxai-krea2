"""Usage pricing and the developer split.

Pure Python so the maths is testable without a deploy. Money is integer cents everywhere;
no floats, no rounding drift. Stripe's own processing fee comes out of the platform side, so
the developer share is computed on gross and the platform carries the card cost — that is the
same model Stripe documents for destination charges with `application_fee_amount`.
"""

from __future__ import annotations

# Overage prices in cents, matching launch/pricing.md (frame 15c, imprint 29c, reel 20c).
PRICES = {"frame": 15, "imprint": 29, "reel": 20}

# Default developer share of gross: 70%, the same split the platform subscription programme
# pays. Override with STRIPE_DEVELOPER_BPS (basis points, 0-10000).
DEFAULT_DEVELOPER_BPS = 7000


def clamp_bps(bps) -> int:
    try:
        value = int(bps)
    except (TypeError, ValueError):
        return DEFAULT_DEVELOPER_BPS
    return max(0, min(10000, value))


def charge_for(usage: dict, prices: dict | None = None) -> int:
    """Gross cents for a usage dict like {"frame": 3, "reel": 1}."""
    table = prices or PRICES
    total = 0
    for kind, count in (usage or {}).items():
        try:
            total += int(table.get(kind, 0)) * max(0, int(count))
        except (TypeError, ValueError):
            continue
    return total


def split_cents(gross_cents: int, developer_bps: int = DEFAULT_DEVELOPER_BPS) -> dict:
    """Integer split. The developer share is floored, so the platform never under-charges."""
    gross = max(0, int(gross_cents))
    bps = clamp_bps(developer_bps)
    developer = gross * bps // 10000
    return {
        "gross_cents": gross,
        "developer_cents": developer,
        "platform_cents": gross - developer,
        "developer_bps": bps,
    }


def stripe_params(amount_cents: int, developer_bps: int, destination: str,
                  currency: str = "usd") -> dict:
    """Form body for a destination charge: the developer is paid, the platform keeps the fee.

    With `application_fee_amount`, the full amount moves to `transfer_data[destination]` and
    the fee returns to the platform — which is exactly "developer gets their share directly".
    """
    split = split_cents(amount_cents, developer_bps)
    return {
        "amount": str(split["gross_cents"]),
        "currency": currency,
        "automatic_payment_methods[enabled]": "true",
        "application_fee_amount": str(split["platform_cents"]),
        "transfer_data[destination]": destination,
    }
