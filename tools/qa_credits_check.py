"""Offline checks for orbit_qa and orbit_credits.

No network, no GPU, no keys. The point is to pin the arithmetic that decides price — a
retry budget, a cost per accepted frame, a rollover cap, an annual plan that still clears its
floor — so a storefront number cannot drift away from the economics behind it.

    python tools/qa_credits_check.py
    python tools/qa_credits_check.py --tables   # print the generated plan + credit tables
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import orbit_credits as C  # noqa: E402
import orbit_qa as Q  # noqa: E402

FAILS: list[str] = []


def check(name: str, got, want) -> None:
    if got != want:
        FAILS.append(f"{name}: got {got!r}, want {want!r}")
        print(f"  FAIL {name}: {got!r} != {want!r}")
    else:
        print(f"  ok   {name} = {got!r}")


def near(name: str, got: float, want: float, tol: float = 0.01) -> None:
    if abs(float(got) - float(want)) > tol:
        FAILS.append(f"{name}: got {got!r}, want ~{want!r}")
        print(f"  FAIL {name}: {got!r} != ~{want!r}")
    else:
        print(f"  ok   {name} = {got!r}")


def main() -> int:
    print("scoring a judge verdict")
    check("8/10 parsed", Q.parse_score("Score: 8/10 for gallery display."), 8.0)
    check("out of 10 parsed", Q.parse_score("I would say 6.5 out of 10."), 6.5)
    check("no number -> review", Q.parse_score("sharp and well formed"), None)
    check("8 accepts", Q.verdict(8.0, []), "accept")
    check("6 with a fixable defect retries", Q.verdict(6.0, ["anatomy"]), "retry")
    check("4 with no defect is rejected", Q.verdict(4.0, []), "reject")
    check("4 with a fixable defect still retries", Q.verdict(4.0, ["artifact"]), "retry")
    check("a policy defect outranks a 9", Q.verdict(9.0, ["content"]), "refuse")
    check("missing score is reviewed", Q.verdict(None, []), "review")
    check("cheapest fix first", [r["action"] for r in Q.plan_for(["detail", "anatomy"])],
          ["reseat", "upscale"])

    print("\nretry budget is bounded by the price")
    check("a 1c frame on $1 carries the cap", Q.attempts_affordable(100, 1), 3)
    check("a 40c attempt leaves one try", Q.attempts_affordable(100, 40), 1)
    check("a 60c attempt leaves none", Q.attempts_affordable(100, 60), 0)

    print("\nexpected work and the cost of an accepted frame")
    near("p_within(0.5, 3)", Q.p_within(0.5, 3), 0.875, 1e-9)
    near("expected_attempts(0.5, 3)", Q.expected_attempts(0.5, 3), 1.75, 1e-9)
    near("expected_attempts(1.0, 3)", Q.expected_attempts(1.0, 3), 1.0, 1e-9)
    check("cost per accepted at 62%", Q.cost_per_accepted(5, 0.62), 8)
    check("retries do not change cost per accepted",
          Q.cost_per_accepted(5, 0.62, 3), Q.cost_per_accepted(5, 0.62, 1))

    print("\nwhat the QA promise is worth on a $1 order")
    cmp = Q.compare(100, 5, 0.62, attempts=3)
    print(f"  single shot: revenue {cmp['before']['revenueCents']}c "
          f"cost {cmp['before']['costCents']}c margin {cmp['before']['marginCents']}c")
    print(f"  3 attempts : revenue {cmp['after']['revenueCents']}c "
          f"cost {cmp['after']['costCents']}c margin {cmp['after']['marginCents']}c")
    check("three attempts beat one shot", cmp["deltaCents"] > 0, True)
    check("delta is refunds avoided, not GPU saved",
          cmp["after"]["costCents"] >= cmp["before"]["costCents"], True)

    print("\nthe gate refuses a price that cannot carry the promise")
    check("30c order can retry", Q.gate(30, 5, 0.62, 2)["allow"], True)
    thin = Q.gate(6, 5, 0.5)
    check("6c order is refused", thin["allow"], False)
    check("and says why", thin["reason"], "price cannot carry even one attempt")

    print("\nQA sold as a product")
    sold = Q.sell("certified", 100, 5, 0.62)
    check("certified fee", sold["feeCents"], 6)
    check("certified keeps a margin", sold["marginCents"] > 0, True)
    check("studio is dearer than certified", Q.sell("studio", 100, 5, 0.62)["feeCents"] > 6, True)
    check("deliver does not judge, so it does not lose a cent",
          Q.sell("deliver", 100, 5, 0.62)["judgeCents"], 0)

    print("\nthe audit record is deterministic and tamper-evident")
    a = Q.audit(b"frame-bytes", 8.2, [], "modal", "o1")
    b = Q.audit(b"frame-bytes", 8.2, [], "modal", "o1")
    check("same bytes same digest", a["sha256"] == b["sha256"], True)
    check("digest is sha256", len(a["sha256"]), 64)
    check("audit states the verdict", a["verdict"], "accept")

    print("\nplans at full use")
    for pid in C.PLANS:
        row = C.unit(pid)
        print(f"  {pid:<7} ${row['priceCents'] / 100:>6.2f}  {row['credits']:>5} credits  "
              f"cost ${row['costCents'] / 100:>6.2f}  margin {row['marginBps'] / 100:>5.1f}%  "
              f"{row['perCreditCents']:.2f}c/credit  floor={row['clearsFloor']}")
    check("comet credits", C.included_credits("comet"), 200)
    check("pulsar credits include fits and reels", C.included_credits("pulsar"), 790)
    check("a paid plan clears the floor", C.unit("comet")["clearsFloor"], True)
    check("quasar clears the floor", C.unit("quasar")["clearsFloor"], True)
    check("comet annual still clears", C.unit("comet", annual=True)["clearsFloor"], True)
    check("pulsar annual does not — two free months on a full plan is a loss",
          C.unit("pulsar", annual=True)["clearsFloor"], False)

    print("\nbrought-own-key is the high-margin line")
    for pid in ("comet", "pulsar", "quasar"):
        row = C.unit(pid, byok=True)
        print(f"  {pid:<7} margin {row['marginBps'] / 100:>5.1f}% at full use")
        check(f"{pid} byok beats the normal plan", row["marginBps"] > C.unit(pid)["marginBps"], True)
    check("byok stretches credits", C.cost_of({"frame": 10}, byok=True)["credits"], 4)

    print("\ntop-ups never undercut the floor")
    for size in sorted(C.TOPUPS):
        row = C.topup(size)
        print(f"  {row['credits']:>5} credits ${row['priceCents'] / 100:>6.2f} "
              f"({row['perCreditCents']:.2f}c) margin {row['marginBps'] / 100:>5.1f}%")
        check(f"{size}-credit pack clears the floor", row["clearsFloor"], True)

    print("\nspend order and rollover")
    out = C.spend({"included": 10, "rollover": 5, "topup": 3}, 12)
    check("included spent first", out["used"], {"included": 10, "rollover": 2, "topup": 0})
    check("bought credits survive", out["balance"]["topup"], 3)
    check("a short balance is reported", C.spend({"included": 1}, 3)["short"], 2)
    rolled = C.grant("comet", {"included": 150, "rollover": 0, "topup": 0})
    check("unused credits roll over", rolled["rollover"], 150)
    capped = C.grant("comet", {"included": 900, "rollover": 400, "topup": 0})
    check("rollover is capped at one month", capped["rollover"], 200)
    check("a free plan rolls nothing", C.grant("guest", {"included": 5})["rollover"], 0)
    gone = C.expire({"rollover": 40, "expires": 100}, now=200)
    check("expired rollover is dropped", gone["rollover"], 0)
    check("off-peak pays in credits", C.offpeak(100), 125)
    ref = C.referral(9900)
    print(f"  referral on a $99 first month: {ref['credits']} credits "
          f"(worth ${ref['valueCents'] / 100:.2f}, costs ${ref['costCents'] / 100:.2f})")
    check("referral pays in credits", ref["valueCents"], 1980)

    print("\ncredit earned or saved on other hosts")
    best = C.earn_table()
    for row in best:
        print(f"  {row['id']:<20} ${row['cashCents'] / 100:>6.2f}  "
              f"{row['minutesMonth']:>4} min  ${row['effectiveHourly']:>7.3f}/h  "
              f"verified={row['verified']}")
    near("HF PRO is ~45c per Blackwell hour",
         [r for r in best if r["id"] == "huggingface_pro"][0]["effectiveHourly"], 0.45, 0.01)
    check("nothing free is reported as a negative cost",
          all(r["effectiveHourly"] >= 0 for r in best), True)

    print("\nthe generated design is complete and self-consistent")
    sheet = C.design()
    check("every plan priced", len(sheet["plans"]), 4)
    check("every pack priced", len(sheet["topups"]), 3)
    check("floor travels with the sheet", sheet["minMarginBps"], 5500)

    if FAILS:
        print(f"\n{len(FAILS)} FAILED")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    if "--tables" in sys.argv:
        sheet = C.design()
        print("plans")
        for row in sheet["plans"]:
            print(f"  {row['label']:<7} ${row['priceCents'] / 100:>7.2f}  {row['credits']:>5}cr  "
                  f"cost ${row['costCents'] / 100:>7.2f}  margin {row['marginBps'] / 100:>5.1f}%  "
                  f"annual ${row['annualRow']['priceCents'] / 100:>8.2f} "
                  f"({row['annualRow']['marginBps'] / 100:.1f}%)  "
                  f"byok {row['byokRow']['marginBps'] / 100:.1f}%")
        print("\nQA tiers")
        for name in Q.TIERS:
            row = Q.sell(name, 100, 5, 0.62)
            print(f"  {row['label']:<10} fee {row['feeCents']}c  judge {row['judgeCents']}c  "
                  f"margin {row['marginCents']}c  attempts {row['attempts']}")
        print("\nplatform credit")
        for row in sheet["platform"]:
            print(f"  {row['label']:<34} ${row['cashCents'] / 100:>6.2f} "
                  f"{row['minutesMonth']:>4}min  ${row['effectiveHourly']:>7.3f}/h")
        sys.exit(0)
    sys.exit(main())
