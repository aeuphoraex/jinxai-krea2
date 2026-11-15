"""Offline checks for orbit_engine: routing, margin arithmetic and the HTTP clients.

No network, no keys, no GPU. The clients are driven with `_post_json` / `_get_json` replaced by
fakes that return the shapes the platforms document, so a parsing mistake surfaces here instead
of in a client's order.

    python tools/engine_check.py            # all checks
    python tools/engine_check.py --rates    # the price table, with verified flags
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import orbit_engine as E  # noqa: E402

FAILS: list[str] = []


def check(name: str, got, want) -> None:
    if got != want:
        FAILS.append(f"{name}: got {got!r}, want {want!r}")
        print(f"  FAIL {name}: {got!r} != {want!r}")
    else:
        print(f"  ok   {name} = {got!r}")


def near(name: str, got: float, want: float, tol: float = 0.005) -> None:
    if abs(float(got) - float(want)) > tol:
        FAILS.append(f"{name}: got {got!r}, want ~{want!r}")
        print(f"  FAIL {name}: {got!r} != ~{want!r}")
    else:
        print(f"  ok   {name} = {got!r}")


def main() -> int:
    print("provider table")
    print(f"  {len(E.PROVIDERS)} providers, render hosts {E.RENDER_HOSTS}")
    for pid in E.RENDER_HOSTS:
        spec = E.PROVIDERS[pid]
        print(f"  {pid:<12} ${E.rate(pid):.6f}/s  +{E.per_task(pid):.4f}/task  "
              f"verified={spec['verified']}  modelled {E.estimate_seconds(pid, 1280, 1600):.0f}s")

    print("\nconfiguration gates routing")
    empty = {k: "" for pid in E.PROVIDERS for k in (E.PROVIDERS[pid]["env"] or ())}
    check("nothing configured -> modal", E.choose({"kind": "image"}, 1.0, env=empty), "modal")
    rh = dict(empty, RUNNINGHUB_API_KEY="k", RUNNINGHUB_WORKFLOW_ID="1904")
    check("runninghub configured -> runninghub", E.choose({"kind": "image"}, 1.0, env=rh), "runninghub")
    mimic = dict(empty, MIMICPC_COMFY_URL="https://x.trycloudflare.com")
    check("only mimicpc configured -> mimicpc", E.choose({"kind": "image"}, 1.0, env=mimic), "mimicpc")
    pod = dict(empty, RUNPOD_API_KEY="k", RUNPOD_ENDPOINT_ID="ep1")
    check("only runpod configured -> runpod", E.choose({"kind": "image"}, 1.0, env=pod), "runpod")
    check("preferred honoured", E.choose({"kind": "image"}, 1.0, preferred="modal", env={
        **rh, "MIMICPC_COMFY_URL": "https://x.trycloudflare.com"}), "modal")

    print("\nmargin arithmetic")
    q = E.quote("image", 1.00, "modal", 1280, 1600)
    print(f"  modal 1280x1600: {q['seconds']}s -> ${q['costUsd']} cost, "
          f"${q['marginUsd']} margin, {q['marginBps']} bps")
    near("modal image cost", q["costUsd"], round(0.001097 * q["seconds"], 2), 0.02)
    check("modal image still profitable", q["marginBps"] >= E.MIN_MARGIN_BPS, True)
    cheap = E.quote("image", 1.00, "runninghub", 1280, 1600)
    check("managed workflow is cheaper than an H100", cheap["costUsd"] < q["costUsd"], True)

    print("\nmargin floor refuses a losing order")
    thin = E.verdict({"kind": "image"}, 0.03, "modal")
    check("3c order on modal is not eligible", thin["eligible"], False)
    check("thin order falls back to modal", E.choose({"kind": "image"}, 0.03, env=rh), "modal")

    print("\nsettle is integer cents")
    warm = E.quote("image", 1.00, "modal", 1280, 1600, cold=False)
    cold = E.quote("image", 1.00, "modal", 1280, 1600, cold=True)
    print(f"  modal warm {warm['seconds']}s ${warm['costUsd']} · cold {cold['seconds']}s ${cold['costUsd']}")
    check("a cold start costs more than a warm frame", cold["costUsd"] > warm["costUsd"], True)
    check("runpod is modelled cold by default", E.PROVIDERS["runpod"]["cold_probable"], True)
    check("mimicpc is modelled warm", E.PROVIDERS["mimicpc"]["cold_probable"], False)
    book = E.settle(1.00, "modal", 33.6, 2_400_000)
    check("price cents", book["priceCents"], 100)
    check("cost cents", book["costCents"], round(0.001097 * 33.6 * 100))
    check("margin = price - cost", book["marginCents"], book["priceCents"] - book["costCents"])
    check("bytes carried", book["bytes"], 2_400_000)

    print("\nrate override")
    os.environ["ENGINE_RATE_RUNPOD"] = "0.0007"
    near("env beats table", E.rate("runpod"), 0.0007, 1e-9)
    os.environ.pop("ENGINE_RATE_RUNPOD")
    near("table restored", E.rate("runpod"), E.PROVIDERS["runpod"]["rate"], 1e-9)

    print("\nplan sorts by cost")
    plan = E.plan("image", 1.00, 1000)
    costs = [row["costEach"] for row in plan["rows"]]
    check("ascending", costs == sorted(costs), True)
    print("  " + " | ".join(f"{r['provider']} ${r['costEach']}" for r in plan["rows"]))

    print("\nrunninghub client")
    seen: dict = {}

    def fake_post(url, body, headers=None, timeout=20):
        seen.setdefault("urls", []).append(url)
        if url.endswith("/task/openapi/create"):
            seen["body"] = body
            return {"code": 0, "data": {"taskId": "T1"}}
        if url.endswith("/task/openapi/status"):
            return {"code": 0, "data": {"status": "SUCCESS"}}
        return {"code": 0, "data": [{"fileUrl": "https://rh.example/a.png"}]}

    E._post_json = fake_post
    rhc = E.RunningHub(api_key="k", workflow_id="W1")
    task = rhc.create({"prompt": "a portrait", "seed": 42, "width": 1280, "height": 1600,
                       "kind": "image"})
    check("task id", task, "T1")
    fields = {(r["nodeId"], r["fieldName"]): r["fieldValue"] for r in seen["body"]["nodeInfoList"]}
    check("prompt routed to text node", fields[("6", "text")], "a portrait")
    check("seed routed to sampler", fields[("3", "seed")], 42)
    check("width routed to latent", fields[("5", "width")], 1280)
    check("outputs url", rhc.wait("T1"), ["https://rh.example/a.png"])

    print("\nrunpod client")
    def fake_get(url, headers=None, timeout=20):
        if "/status/" in url:
            return {"status": "COMPLETED",
                    "output": {"images": [{"data": "data:image/png;base64,AAAA"}]}}
        return {}

    E._get_json = fake_get
    backend = E.RunPod(api_key="k", endpoint="ep1")
    check("runpod returns a data uri", backend.wait("J1"), ["data:image/png;base64,AAAA"])

    print("\ncomfy workflow patch")
    template = {"6": {"class_type": "CLIPTextEncode", "inputs": {"text": "old"}},
                "3": {"class_type": "KSampler", "inputs": {"seed": 1}},
                "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512}}}
    wf = E.comfy_workflow({"prompt": "new", "seed": 9, "width": 1280, "height": 1600}, template)
    check("text patched", wf["6"]["inputs"]["text"], "new")
    check("seed patched", wf["3"]["inputs"]["seed"], 9)
    check("width patched", wf["5"]["inputs"]["width"], 1280)
    check("template untouched", template["6"]["inputs"]["text"], "old")

    print("\nhuggingface space address")
    check("space id -> url", E.HFSpace(space="jinxai/velvet-studio").base,
          "https://jinxai-velvet-studio.hf.space")

    print("\nninguna otra cosa")
    if FAILS:
        print(f"\n{len(FAILS)} FAILED")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    if "--rates" in sys.argv:
        for pid, spec in E.PROVIDERS.items():
            print(f"{pid:<12} {spec['kind']:<17} ${E.rate(pid):.6f}/s "
                  f"+${E.per_task(pid):.4f}/task  verified={spec['verified']}")
        sys.exit(0)
    sys.exit(main())
