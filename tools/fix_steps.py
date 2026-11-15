"""Repair commission receipts that claim a schedule `generate` never ran.

`generate` pins the sampling schedule at 8 steps for this distilled core and ignores the
`steps` field, so the first two Velvet orders recorded a tier number (20) that no render
used. This rewrites those records to the schedule that actually ran, in place, on the shared
Modal Dict. Nothing is re-rendered and no wallet is touched.

    ./.venv/Scripts/modal.exe run tools/fix_steps.py
"""

import modal

# A separate app: the point is to edit a Dict, not to redeploy the studio.
app = modal.App("jinxai-receipt-fix")
commissions = modal.Dict.from_name("orbital-commissions", create_if_missing=True)

REAL_STEPS = 8


@app.function()
def fix() -> dict:
    fixed = []
    for key in commissions.keys():
        rec = commissions.get(key)
        if not isinstance(rec, dict):
            continue
        claimed = int(rec.get("steps") or 0)
        if claimed <= REAL_STEPS:
            continue
        rec["steps"] = REAL_STEPS
        rec["steps_note"] = (
            f"corrected from {claimed}: generate pins the schedule at {REAL_STEPS} steps; "
            "the tier field was never applied"
        )
        commissions[key] = rec
        fixed.append(key)
    return {"fixed": fixed, "count": len(fixed)}


@app.local_entrypoint()
def main() -> None:
    print(fix.remote())
