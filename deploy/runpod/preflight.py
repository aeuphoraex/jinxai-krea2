"""Refuse to publish a Hub release whose test GPU has no instances.

A Hub build does not start a container when the pin cannot be placed; it fails with

    There were no instances available for your specified GPU NVIDIA A40 nor on any of the
    fallback GPUs NVIDIA RTX A6000, NVIDIA L40.

which says nothing about the worker, and which the offline suite cannot see because it is a fact
about RunPod's fleet this minute. On 2026-11-16 that error shipped in v0.1.11: the pin named an
A40 that read `MEDIUM` on `product=SERVERLESS` while the *pod* fleet had it at `NONE`, together
with both of the fallbacks the Hub picked.

Two things this encodes, both measured rather than assumed:

- **The Hub derives its own same-class fallbacks from the pin.** For the A40 pin they were the
  RTX A6000 and the L40 — three 48 GB cards, so "availability" is a property of a class, not of a
  card. A pin passes here when any card in its VRAM class has instances and an allowed driver.
- **`allowedCudaVersions` is matched exactly, per host.** A card can have instances and still be
  unusable because its only driver is outside the list. That outage also shipped once.

    python deploy/runpod/preflight.py                       # the pin in .runpod/tests.json
    python deploy/runpod/preflight.py --device "NVIDIA L40S"
    python deploy/runpod/preflight.py --release v0.1.13 --notes-file out/notes.md

`--release` runs the tag and the GitHub release *only* after the gate passes, and refuses when
`.runpod/tests.json` has uncommitted changes — the Hub reads the tag, so publishing a dirty pin
would release the previous one and look like a passing gate.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CATALOG = "https://api.runpod.io/v2/catalog/gpus?include=AVAILABILITY&product={product}"


def tests_config() -> dict:
    with open(os.path.join(ROOT, ".runpod", "tests.json"), encoding="utf-8") as fh:
        return json.load(fh)["config"]


def fetch(product: str = "POD", timeout: int = 30) -> list[dict]:
    """Every GPU with current stock for `product`.

    `POD` is the fleet a Hub test pod schedules on; `SERVERLESS` is the listing's own pool. They
    are different fleets — an A40 was MEDIUM on one and NONE on the other on the same day — so the
    product to read is the one the thing being published will actually run on.

    `rest.runpod.io/v2` 301s to the API docs page, so `curl -L` stores HTML with a 200. This host
    is the live catalog, and it wants a User-Agent: a bare urllib client gets 403.
    """
    key = (os.environ.get("RUNPOD_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("RUNPOD_API_KEY is not set")
    req = urllib.request.Request(CATALOG.format(product=product))
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("User-Agent", "jinxai-preflight/1")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        doc = json.loads(resp.read().decode("utf-8") or "{}")
    return list(doc.get("gpus") or [])


def _names(entry: dict) -> set[str]:
    return {str(entry.get(field) or "") for field in ("id", "displayName", "name")} - {""}


def resolve(gpus: list[dict], device: str) -> dict | None:
    """The catalog row for `device`, accepting RunPod's short display name.

    `.runpod/tests.json` wants the full id (`NVIDIA GeForce RTX 5090`); the catalog's
    `displayName` is `RTX 5090`. An ambiguous suffix match is refused rather than guessed.
    """
    for entry in gpus:
        if device in _names(entry):
            return entry
    loose = [entry for entry in gpus
             if any(device.endswith(name) or name.endswith(device) for name in _names(entry))]
    return loose[0] if len(loose) == 1 else None


def available_cuda(entry: dict) -> list[str]:
    return sorted(str(row.get("version")) for row in (entry.get("cudaVersions") or [])
                  if isinstance(row, dict) and row.get("available"))


def classmates(gpus: list[dict], entry: dict) -> list[dict]:
    """Same VRAM, different card — the set the Hub is willing to fall back to.

    That is what the observed fallbacks were: A40 (Ampere), RTX A6000 (Ampere) and L40 (Ada) are
    three architectures and one 48 GB class.
    """
    memory = entry.get("memoryInGb")
    if memory is None:
        return []
    mine = _names(entry)
    return [other for other in gpus
            if other is not entry and other.get("memoryInGb") == memory
            and not (mine & _names(other))]


def gate(gpus: list[dict], device: str, allowed_cuda: list[str]) -> dict:
    """Whether a Hub test pinned to `device` could be placed and started. Pure; no network."""
    allowed = [str(v) for v in allowed_cuda]
    result = {"device": device, "ok": False, "reasons": [], "warnings": [],
              "pin": None, "class": []}
    entry = resolve(gpus, device)
    if entry is None:
        result["reasons"].append(f"{device!r} is not a GPU type in the catalog")
        return result

    def row(item: dict) -> dict:
        return {"name": str(item.get("id") or item.get("displayName") or ""),
                "availability": str(item.get("availability") or "UNKNOWN"),
                "cuda": available_cuda(item)}

    result["pin"] = row(entry)
    peers = [row(other) for other in classmates(gpus, entry)]
    result["class"] = peers
    if not peers:
        result["warnings"].append("no same-VRAM peer in the catalog; no fallback to reason about")

    candidates = [result["pin"]] + [peer for peer in peers if peer["availability"] != "NONE"]
    usable = [row for row in candidates if row["availability"] != "NONE"
              and set(row["cuda"]) & set(allowed)]

    if result["pin"]["availability"] == "NONE":
        fallback = ", ".join(f"{peer['name']} ({peer['availability']})" for peer in peers) or "none"
        result["warnings"].append(
            f"the pin itself has no instances; the Hub falls back within its class: {fallback}")

    if not usable:
        sold_out = [row["name"] for row in [result["pin"]] + peers
                    if row["availability"] == "NONE"]
        result["reasons"].append(
            f"no card in the {entry.get('memoryInGb')} GB class has instances with a CUDA "
            f"version in {allowed}: sold out = {', '.join(sold_out) or 'none'}")
        return result

    if not (set(result["pin"]["cuda"]) & set(allowed)) and result["pin"]["availability"] != "NONE":
        result["warnings"].append(
            f"the pin reports {result['pin']['cuda']} and the allowed list is {allowed}; it would "
            f"only schedule on a class peer")
    result["ok"] = True
    return result


def dirty_pin() -> bool:
    """True when `.runpod/tests.json` differs from HEAD — a tag would publish the old pin."""
    done = subprocess.run(["git", "status", "--porcelain", "--", ".runpod/tests.json"],
                          cwd=ROOT, capture_output=True, text=True)
    return bool(done.stdout.strip())


def release(tag: str, notes: str, dry_run: bool = False) -> int:
    existing = subprocess.run(["git", "tag", "-l", tag], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip()
    if existing:
        print(f"tag {tag} already exists; bump the version instead of moving a published tag")
        return 2
    if not os.path.exists(notes):
        print(f"notes file {notes} does not exist")
        return 2
    for cmd in (["git", "tag", tag],
                ["git", "push", "origin", "main", "--tags"],
                ["gh", "release", "create", tag, "--title", tag, "--notes-file", notes]):
        print("+", " ".join(cmd))
        if dry_run:
            continue
        done = subprocess.run(cmd, cwd=ROOT)
        if done.returncode:
            print(f"failed: {' '.join(cmd)} -> {done.returncode}")
            return 1
    if dry_run:
        print("dry run: nothing was tagged, pushed or released")
        return 0
    print(f"\nreleased {tag}. The Hub indexes releases, so it will re-run both tests on it.")
    return 0


def main() -> int:
    cfg = tests_config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", default="POD", choices=("POD", "SERVERLESS"),
                        help="POD is what a Hub test pod schedules on (default)")
    parser.add_argument("--device", default=str(cfg.get("gpuTypeId") or ""),
                        help="defaults to tests.json's gpuTypeId")
    parser.add_argument("--cuda", default=",".join(str(v) for v in cfg.get("allowedCudaVersions") or []),
                        help="defaults to tests.json's allowedCudaVersions")
    parser.add_argument("--release", default="", metavar="TAG",
                        help="tag and publish only after the gate passes")
    parser.add_argument("--notes-file", default="out/notes.md")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    device = args.device.strip()
    allowed = [part.strip() for part in args.cuda.split(",") if part.strip()]
    if not device or not allowed:
        print("nothing to check: tests.json carries no gpuTypeId or allowedCudaVersions")
        return 2

    try:
        gpus = fetch(args.product)
    except Exception as exc:              # noqa: BLE001 - report, never mask
        print(f"could not read the {args.product} catalog: {type(exc).__name__}: {exc}")
        return 2

    report = gate(gpus, device, allowed)
    pin = report["pin"] or {}
    print(f"pin      {device}")
    print(f"stock    {pin.get('availability')}   allowed {allowed}")
    print(f"drivers  {pin.get('cuda')}")
    for peer in report["class"]:
        print(f"  class  {peer['name']:<34} {peer['availability']:<5} {peer['cuda']}")
    for warning in report["warnings"]:
        print("note    ", warning)

    if not report["ok"]:
        for reason in report["reasons"]:
            print("FAIL    ", reason)
        print("\nnothing was published. Re-read the catalog for another class, or re-pin "
              ".runpod/tests.json to a card that has stock.")
        return 1

    print(f"\nok: a {args.product} test pinned to {device} can be placed")
    if not args.release:
        print("publish with: python deploy/runpod/preflight.py --release <tag> "
              "--notes-file <file>")
        return 0

    if dirty_pin():
        print("FAIL: .runpod/tests.json has uncommitted changes; commit the pin first, because "
              "the Hub reads the tag and would test the previous one")
        return 1
    return release(args.release, args.notes_file, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
