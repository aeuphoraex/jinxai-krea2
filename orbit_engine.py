"""Where a paid render runs, and what it earns.

The pipeline used to have exactly one answer to "who draws this frame": this Modal deployment.
That is the most expensive answer, because an H100 stays warm for five minutes after the last
order and a single late-night L$250 picture can cost more in idle time than in compute
(`launch/economics.md` §1). The same 8-step Krea 2 graph runs on four other kinds of host,
each with a different bill:

  * `runninghub` — managed ComfyUI workflow API, billed per task, no idle cost.
  * `mimicpc`    — a rented browser ComfyUI, billed per hour whether or not it renders.
  * `runpod`     — serverless worker (billed per second, cold start per request) or a pod.
  * `huggingface`— a free CPU Space that fronts this deployment: zero compute, zero margin cost.
  * `modal`      — this deployment, measured at $0.001097/GPU-second.

So the engine is two things and nothing more: a chooser, and arithmetic. `choose` returns the
cheapest *configured* provider whose estimated cost still leaves the order profitable;
`settle` books revenue, cost and margin against the order so the ledger says what a render
earned instead of what it was hoped to earn.

Prices in this module are dollars, but every booking is integer cents — the same rule
`orbit_econ` follows, so a rounding drift cannot turn a loss into a profit on paper.

Nothing here is enabled by configuration alone: a provider counts as configured only when its
env vars are present (`_env`), so a deploy without keys routes every order to Modal exactly as
before. Importing this module never touches the network; the HTTP clients are called only from
`render()`.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.parse
import urllib.request

# --------------------------------------------------------------------------------------
# The provider table
# --------------------------------------------------------------------------------------
#
# `rate` is $/GPU-second, `per_task` is a fixed $ per render (managed APIs bill in credits, not
# seconds), and the billed amount is the larger of the two — a per-task provider must never look
# free because its second-rate is zero.
#
# `verified` marks a rate read off a page or invoice; an unverified rate is a placeholder that
# belongs to somebody's price list, and the ledger will be wrong until `ENGINE_RATE_<ID>` is set
# to what is actually being charged. `tools/engine_check.py --rates` prints this table with the
# flags so the guess is never mistaken for a measurement.
PROVIDERS: dict[str, dict] = {
    "modal": {
        "label": "Modal · H100 · own pipeline",
        "kind": "serverless-gpu",
        "env": (),                     # always configured: it is this deployment
        "rate": 0.001097,              # $3.9492/h, verified 2026-11
        "per_task": 0.0,
        "verified": True,
        "seconds_1mp": 33.6,           # measured warm, 8 steps, 1024²
        "cold_seconds": 58.0,          # measured 92 s cold incl. weight load, minus the 33.6
        "cold_probable": True,         # scales to zero, so most orders pay the load
        "capabilities": ("lora", "character", "upscale", "private"),
        "note": "Stays warm 300 s after the last order; idle time is the real bill.",
    },
    "runninghub": {
        "label": "RunningHub · managed ComfyUI workflow",
        "kind": "managed-workflow",
        "env": ("RUNNINGHUB_API_KEY", "RUNNINGHUB_WORKFLOW_ID"),
        "rate": 0.0,
        "per_task": 0.0200,            # credits per workflow run — set to the real invoice rate
        "verified": False,
        "seconds_1mp": 26.0,           # their queue + render, not billed by us
        "cold_seconds": 0.0,           # their problem, not our bill
        "cold_probable": False,        # the workflow is already deployed on their side
        "capabilities": ("lora", "workflow"),
        "note": "No idle cost. The workflow must be uploaded once; workflowId is the handle.",
    },
    "mimicpc": {
        "label": "MimicPC · rented ComfyUI (L4/A10)",
        "kind": "rented-gpu",
        "env": ("MIMICPC_COMFY_URL",),
        "rate": 0.000358,              # $1.29/h instant access L4 — the machine runs until stopped
        "per_task": 0.0,
        "verified": False,
        "seconds_1mp": 31.0,
        "cold_seconds": 90.0,          # model load on a fresh app launch
        "cold_probable": False,        # a rented machine is billed until it is stopped, so it is warm
        "capabilities": ("lora", "workflow"),
        "note": "Billed per hour, so the machine must be stopped; API needs a tunnel (cloudflared).",
    },
    "runpod": {
        "label": "RunPod · serverless worker",
        "kind": "serverless-gpu",
        "env": ("RUNPOD_API_KEY", "RUNPOD_ENDPOINT_ID"),
        "rate": 0.000310,              # 24 GB serverless, flexible — set to the invoice rate
        "per_task": 0.0,
        "verified": False,
        "seconds_1mp": 28.0,
        "cold_seconds": 45.0,          # model load on a scaled-to-zero worker
        "cold_probable": True,         # scale-to-zero: an idle endpoint is a cold next request
        "capabilities": ("lora", "character", "upscale"),
        "note": "Per-second billing and scale-to-zero; a cold worker pays the weight load every time.",
    },
    "huggingface": {
        "label": "Hugging Face · Space front desk",
        "kind": "proxy",
        "env": ("HF_SPACE_ID",),
        "rate": 0.0,                   # free CPU hardware, no GPU
        "per_task": 0.0,
        "verified": True,
        "seconds_1mp": 0.0,
        "cold_seconds": 0.0,
        "cold_probable": False,
        "capabilities": ("proxy",),
        "note": "Distributes and collects; the frame is still drawn on whichever GPU host is behind it.",
    },
}

# The routing rule the engine enforces. A provider is only chosen when the order still clears
# this share of gross after its own cost: below it, a render is a loss leader nobody asked for.
MIN_MARGIN_BPS = int(os.environ.get("ENGINE_MIN_MARGIN_BPS", "5500") or "5500")

# Serverless GPU hosts we can actually dispatch a render to. `huggingface` is a proxy, so it
# is never the answer to "who draws this" — the Space calls back into `/api/generate`.
RENDER_HOSTS = ("modal", "runninghub", "mimicpc", "runpod")


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name) or default).strip()


def rate(pid: str) -> float:
    """The $/GPU-second actually being charged, with an env override per provider."""
    key = "ENGINE_RATE_" + str(pid).upper().replace("-", "_")
    raw = _env(key)
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return float(PROVIDERS.get(pid, {}).get("rate") or 0.0)


def per_task(pid: str) -> float:
    key = "ENGINE_TASK_USD_" + str(pid).upper().replace("-", "_")
    raw = _env(key)
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return float(PROVIDERS.get(pid, {}).get("per_task") or 0.0)


def configured(pid: str, env: dict | None = None) -> tuple[bool, str]:
    """(is it usable, the env var that is missing).

    `env` lets a test answer for a machine that is not this one; production reads `os.environ`.
    """
    spec = PROVIDERS.get(pid)
    if not spec:
        return False, "unknown provider"
    getter = (lambda k: str(env.get(k) or "").strip()) if env is not None else _env
    for key in spec.get("env") or ():
        if not getter(key):
            return False, key
    return True, ""


def estimate_seconds(pid: str, width: int = 1024, height: int = 1024,
                     upscale: int = 0, cold: bool | None = None) -> float:
    """How long one frame takes on this host, modelled on the measured 1 MP figure.

    Resolution scales linearly with pixels at fixed steps (the graph is fixed at 8, CFG 0), and
    a refine pass re-runs the schedule on tiles, which measures ~0.6× the base render again.
    `cold=None` means "ask the host": a scale-to-zero endpoint pays the weight load on most
    requests, while a machine that is billed per hour is already warm.
    """
    spec = PROVIDERS.get(pid) or {}
    if cold is None:
        cold = bool(spec.get("cold_probable"))
    work = (max(1, int(width)) * max(1, int(height))) / (1024 * 1024)
    seconds = float(spec.get("seconds_1mp") or 0.0) * work
    if upscale:
        seconds *= 1.6
    if cold:
        seconds += float(spec.get("cold_seconds") or 0.0)
    return round(seconds, 1)


def estimate_cost(pid: str, seconds: float) -> float:
    """Billed dollars for one render: the larger of a per-task fee and per-second GPUs."""
    return round(max(per_task(pid), rate(pid) * max(0.0, float(seconds))), 6)


def quote(kind: str, price_usd: float, pid: str, width: int = 1280, height: int = 1600,
          upscale: int = 0, cold: bool | None = None) -> dict:
    """One provider's economics for one order: price, cost, margin in bps."""
    seconds = estimate_seconds(pid, width, height, upscale, cold)
    cost = estimate_cost(pid, seconds)
    gross = max(0.0, float(price_usd))
    gross_cents = int(round(gross * 100))
    cost_cents = int(round(cost * 100))
    margin = gross_cents - cost_cents
    return {
        "provider": pid,
        "kind": str(kind),
        "priceUsd": round(gross, 2),
        "costUsd": round(cost_cents / 100, 2),
        "marginUsd": round(margin / 100, 2),
        "marginBps": (margin * 10000 // gross_cents) if gross_cents else 0,
        "seconds": seconds,
        "rate": rate(pid),
        "perTask": per_task(pid),
        "verified": bool((PROVIDERS.get(pid) or {}).get("verified")),
    }


def verdict(job: dict, price_usd: float, pid: str, env: dict | None = None) -> dict:
    """The quote plus whether this host is allowed to take the order.

    `env` must be passed through from `choose`: a provider that is configured *for this call*
    must not be judged against the live environment and quietly dropped.
    """
    got = quote(
        str(job.get("kind") or "image"), price_usd, pid,
        int(job.get("width") or 1280), int(job.get("height") or 1600),
        int(job.get("upscale") or 0),
        job.get("cold"),
    )
    ok, missing = configured(pid, env)
    got["configured"] = ok
    got["missing"] = missing
    got["eligible"] = ok and pid in RENDER_HOSTS and got["marginBps"] >= MIN_MARGIN_BPS
    return got


def choose(job: dict, price_usd: float, preferred: str = "", env: dict | None = None) -> str:
    """The cheapest host that keeps the order profitable; Modal when nothing else qualifies.

    A configured `preferred` host wins only if it is eligible itself — pinning a provider must
    not become a way to render at a loss — and Modal is always the fallback because the graph,
    the weights and the adapters are already there.
    """
    rows = []
    for pid in RENDER_HOSTS:
        ok, _missing = configured(pid, env)
        if not ok:
            continue
        got = verdict(job, price_usd, pid, env)
        if not got["eligible"]:
            continue
        rows.append((got["costUsd"], pid))
    rows.sort()
    if preferred:
        for _cost, pid in rows:
            if pid == preferred:
                return pid
    return rows[0][1] if rows else "modal"


def settle(price_usd: float, provider: str, seconds: float, bytes_out: int = 0) -> dict:
    """The money side of a finished render, in integer cents.

    Called once, after the frame exists: a refund path never books revenue, so the ledger cannot
    show a failed order as a sale.
    """
    cost = estimate_cost(provider, seconds)
    gross_cents = int(round(max(0.0, float(price_usd)) * 100))
    cost_cents = int(round(cost * 100))
    margin = gross_cents - cost_cents
    return {
        "provider": provider,
        "priceCents": gross_cents,
        "costCents": cost_cents,
        "marginCents": margin,
        "marginBps": (margin * 10000 // gross_cents) if gross_cents else 0,
        "seconds": round(float(seconds or 0.0), 1),
        "bytes": int(bytes_out or 0),
        "settledAt": int(time.time()),
        "verified": bool((PROVIDERS.get(provider) or {}).get("verified")),
    }


def plan(kind: str, price_usd: float, count: int = 1000, width: int = 1280,
         height: int = 1600, upscale: int = 0) -> dict:
    """What `count` orders would earn on each host — the table a price is chosen against."""
    rows = []
    for pid in RENDER_HOSTS:
        got = quote(kind, price_usd, pid, width, height, upscale)
        rows.append({
            "provider": pid,
            "label": (PROVIDERS[pid] or {}).get("label", pid),
            "costEach": got["costUsd"],
            "costTotal": round(got["costUsd"] * count, 2),
            "revenueTotal": round(price_usd * count, 2),
            "marginTotal": round((price_usd - got["costUsd"]) * count, 2),
            "marginBps": got["marginBps"],
            "configured": configured(pid)[0],
            "verified": got["verified"],
        })
    rows.sort(key=lambda r: r["costEach"])
    return {"kind": kind, "priceUsd": price_usd, "count": int(count), "rows": rows,
            "minMarginBps": MIN_MARGIN_BPS}


# --------------------------------------------------------------------------------------
# HTTP clients — no new dependencies, urllib only
# --------------------------------------------------------------------------------------


def _post_json(url: str, body: dict, headers: dict | None = None, timeout: int = 20) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _get_bytes(url: str, headers: dict | None = None, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, method="GET")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _get_json(url: str, headers: dict | None = None, timeout: int = 20) -> dict:
    return json.loads(_get_bytes(url, headers, timeout).decode("utf-8") or "{}")


def _b64(raw: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


class RunningHub:
    """Managed ComfyUI workflow execution: submit a taskId, poll status, fetch outputs.

    Parameter changes go through `nodeInfoList` — one row per (nodeId, fieldName) — which is
    why the workflow's node ids are configuration (`RUNNINGHUB_NODES`, a JSON file) rather than
    constants here: they belong to the uploaded workflow, not to this code.
    """

    base = "https://www.runninghub.cn"

    def __init__(self, api_key: str = "", workflow_id: str = "", nodes: dict | None = None):
        self.api_key = api_key or _env("RUNNINGHUB_API_KEY")
        self.workflow_id = workflow_id or _env("RUNNINGHUB_WORKFLOW_ID")
        self.nodes = nodes if nodes is not None else node_map()

    def _call(self, path: str, body: dict) -> dict:
        got = _post_json(self.base + path, body,
                         {"Host": "www.runninghub.cn", "Authorization": "Bearer " + self.api_key})
        if str(got.get("code")) not in ("0", "200", ""):
            raise RuntimeError(f"runninghub {path}: {got.get('msg') or got.get('code')}")
        return got.get("data") if isinstance(got.get("data"), dict) else got

    def create(self, job: dict) -> str:
        rows = []
        for name, spec in self.nodes.items():
            if not spec or name == "image":
                continue
            value = job.get(name)
            if value is None:
                continue
            rows.append({"nodeId": str(spec["nodeId"]), "fieldName": spec["fieldName"],
                         "fieldValue": value})
        got = self._call("/task/openapi/create", {
            "apiKey": self.api_key, "workflowId": self.workflow_id,
            "nodeInfoList": rows, "taskType": "ASYNC",
        })
        task = str(got.get("taskId") or got.get("taskID") or "")
        if not task:
            raise RuntimeError("runninghub returned no taskId")
        return task

    def wait(self, task: str, timeout: int = 240) -> list[str]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            got = self._call("/task/openapi/status", {"apiKey": self.api_key, "taskId": task})
            state = str(got.get("status") or got.get("taskStatus") or "").upper()
            if state in ("SUCCESS", "SUCCEEDED", "COMPLETED"):
                break
            if state in ("FAILED", "ERROR", "CANCELLED"):
                raise RuntimeError(f"runninghub task {task} {state.lower()}")
            time.sleep(2.0)
        else:
            raise TimeoutError(f"runninghub task {task} still running after {timeout}s")
        out = self._call("/task/openapi/outputs", {"apiKey": self.api_key, "taskId": task})
        rows = out.get("data") if isinstance(out.get("data"), list) else out
        rows = rows if isinstance(rows, list) else []
        urls = [str(r.get("fileUrl") or r.get("url") or "") for r in rows if isinstance(r, dict)]
        urls = [u for u in urls if u.startswith("http")][:1]
        if not urls:
            raise RuntimeError("runninghub task produced no image url")
        return urls


class ComfyUI:
    """A rented or pod ComfyUI (MimicPC, RunPod pod) spoken to over its own HTTP API.

    ComfyUI has no auth of its own, so the base URL is expected to be a tunnel or a proxied
    address (`MIMICPC_COMFY_URL`), and an optional bearer is sent when one is configured.
    """

    def __init__(self, base: str = "", token: str = ""):
        self.base = (base or _env("MIMICPC_COMFY_URL") or _env("COMFY_URL")).rstrip("/")
        self.token = token or _env("COMFY_TOKEN")

    def _headers(self) -> dict:
        return {"Authorization": "Bearer " + self.token} if self.token else {}

    def create(self, job: dict) -> str:
        wf = comfy_workflow(job)
        got = _post_json(self.base + "/prompt", {"prompt": wf, "client_id": "orbital-engine"},
                         self._headers())
        pid = str(got.get("prompt_id") or "")
        if not pid:
            raise RuntimeError(f"comfyui rejected the workflow: {got.get('error') or got}")
        return pid

    def wait(self, prompt_id: str, timeout: int = 300) -> list[str]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            hist = _get_json(self.base + f"/history/{prompt_id}", self._headers())
            row = hist.get(prompt_id) if isinstance(hist, dict) else None
            if isinstance(row, dict) and row.get("outputs"):
                urls = []
                for node in (row.get("outputs") or {}).values():
                    for image in (node or {}).get("images") or []:
                        name = str(image.get("filename") or "")
                        if not name:
                            continue
                        query = urllib.parse.urlencode({
                            "filename": name,
                            "subfolder": image.get("subfolder") or "",
                            "type": image.get("type") or "output",
                        })
                        urls.append(f"{self.base}/view?{query}")
                if urls:
                    return urls[:1]
            time.sleep(2.0)
        raise TimeoutError(f"comfyui prompt {prompt_id} produced nothing in {timeout}s")


class RunPod:
    """RunPod serverless: `/run` a job, poll `/status`, read base64 out of `output`.

    The worker (`handler.py`) returns `{"images": [{"data": "<b64>"}]}`, the same
    shape this deployment's `generate` returns, so the order record does not care who drew it.
    """

    base = "https://api.runpod.ai/v2"

    def __init__(self, api_key: str = "", endpoint: str = ""):
        self.api_key = api_key or _env("RUNPOD_API_KEY")
        self.endpoint = endpoint or _env("RUNPOD_ENDPOINT_ID")

    def _headers(self) -> dict:
        return {"Authorization": "Bearer " + self.api_key}

    def create(self, job: dict) -> str:
        got = _post_json(f"{self.base}/{self.endpoint}/run", {"input": job}, self._headers())
        rid = str(got.get("id") or "")
        if not rid:
            raise RuntimeError("runpod returned no job id")
        return rid

    def wait(self, job_id: str, timeout: int = 300) -> list[str]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            got = _get_json(f"{self.base}/{self.endpoint}/status/{job_id}", self._headers())
            state = str(got.get("status") or "").upper()
            if state == "COMPLETED":
                out = got.get("output") or {}
                images = out.get("images") if isinstance(out, dict) else None
                first = (images or [{}])[0] if isinstance(images, list) else {}
                data = str((first or {}).get("data") or "")
                if data:
                    return [data]                     # already a data: URI
                url = str((first or {}).get("url") or (out.get("url") if isinstance(out, dict) else "") or "")
                if url.startswith("http"):
                    return [url]
                raise RuntimeError("runpod job completed without an image")
            if state in ("FAILED", "CANCELLED", "TIMED_OUT"):
                raise RuntimeError(f"runpod job {job_id} {state.lower()}: {got.get('error')}")
            time.sleep(2.0)
        raise TimeoutError(f"runpod job {job_id} still running after {timeout}s")


class HFSpace:
    """A Gradio Space called over its own REST surface (no `gradio_client` dependency).

    A Space is the free front desk: it takes the prompt, calls `/api/generate` on this
    deployment, and returns the frame. It is never a render host, so `choose` ignores it.
    """

    def __init__(self, space: str = "", token: str = ""):
        space = (space or _env("HF_SPACE_ID")).strip("/")
        self.base = space if space.startswith("http") else (
            "https://" + space.replace("/", "-").replace(".", "-") + ".hf.space" if space else "")
        self.token = token or _env("HF_TOKEN")

    def call(self, api_name: str, payload: list, timeout: int = 180) -> list:
        headers = {"Authorization": "Bearer " + self.token} if self.token else {}
        got = _post_json(f"{self.base}/gradio_api/call/{api_name.lstrip('/')}",
                         {"data": payload}, headers)
        event = str(got.get("event_id") or "")
        if not event:
            raise RuntimeError("space did not return an event id")
        url = f"{self.base}/gradio_api/call/{api_name.lstrip('/')}/{event}"
        req = urllib.request.Request(url, method="GET")
        for key, value in headers.items():
            req.add_header(key, value)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("data:"):
                    body = line[5:].strip()
                    if body and body != "null":
                        try:
                            return json.loads(body)
                        except ValueError:
                            continue
        raise RuntimeError("space stream ended without a result")


# --------------------------------------------------------------------------------------
# Workflow plumbing
# --------------------------------------------------------------------------------------


def node_map() -> dict:
    """Prompt/seed/size/LoRA node ids from `RUNNINGHUB_NODES` (a JSON file or inline JSON).

    Defaults match `comfy/_ref/krea2_t2i.json`: the text encode (6), the sampler (3), the empty
    latent (5) and the style LoRA loader (15). A different workflow is a different map, which
    is exactly why this is not hard-coded into the client.
    """
    path = _env("RUNNINGHUB_NODES")
    if path:
        try:
            if path.lstrip().startswith("{"):
                return json.loads(path)
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    return {
        "prompt": {"nodeId": "6", "fieldName": "text"},
        "seed": {"nodeId": "3", "fieldName": "seed"},
        "width": {"nodeId": "5", "fieldName": "width"},
        "height": {"nodeId": "5", "fieldName": "height"},
        "lora": {"nodeId": "15", "fieldName": "lora_name"},
        "scale": {"nodeId": "15", "fieldName": "strength_model"},
    }


def comfy_workflow(job: dict, template: dict | None = None) -> dict:
    """An API-format workflow for `job`, patched in place.

    `template` is the API-format JSON ComfyUI's "Save (API)" produces; it defaults to
    `COMFY_WORKFLOW` (a path) so a rented machine can be re-pointed at a different graph
    without a code change.
    """
    wf = template if isinstance(template, dict) else None
    if wf is None:
        path = _env("COMFY_WORKFLOW")
        if not path:
            raise RuntimeError("COMFY_WORKFLOW is not set to an API-format workflow json")
        with open(path, encoding="utf-8") as f:
            wf = json.load(f)
    wf = json.loads(json.dumps(wf))               # a copy: the template is reused per order
    nodes = node_map()
    for name in ("prompt", "width", "height", "seed"):
        spec = nodes.get(name)
        value = job.get(name)
        if not spec or value is None:
            continue
        node = wf.get(str(spec["nodeId"]))
        if isinstance(node, dict):
            node.setdefault("inputs", {})[spec["fieldName"]] = value
    if job.get("lora"):
        for name in ("lora", "scale"):
            spec = nodes.get(name)
            if spec and wf.get(str(spec["nodeId"])) is not None:
                wf[str(spec["nodeId"])]["inputs"][spec["fieldName"]] = (
                    job.get(name) if name == "lora" else float(job.get("scale") or 1.0))
    return wf


def _fetch_image(url: str, headers: dict | None = None) -> bytes:
    if url.startswith("data:"):
        return base64.b64decode(url.split(",", 1)[-1])
    return _get_bytes(url, headers, 60)


def render(job: dict, provider: str) -> dict:
    """Draw one frame on `provider` and return this deployment's `generate` shape.

    The return value is the contract `commission_render` already understands, so the order
    record gains an engine and a bill without any other change: resolving a URL, timing the
    task and filling `images[0]` is all a remote host has to supply.
    """
    started = time.time()
    if provider == "runninghub":
        client = RunningHub()
        urls = client.wait(client.create(job))
        raw = _fetch_image(urls[0])
        steps = 8
    elif provider == "mimicpc":
        client = ComfyUI()
        urls = client.wait(client.create(job))
        raw = _fetch_image(urls[0], client._headers())
        steps = 8
    elif provider == "runpod":
        client = RunPod()
        urls = client.wait(client.create(job))
        raw = _fetch_image(urls[0], {"Authorization": "Bearer " + client.api_key}
                           if urls[0].startswith("http") else None)
        steps = 8
    else:
        raise RuntimeError(f"{provider} is not a render host")
    seconds = round(time.time() - started, 2)
    return {
        "images": [{
            "data": _b64(raw),
            "seed": job.get("seed"),
            "width": int(job.get("width") or 0),
            "height": int(job.get("height") or 0),
            "seconds": seconds,
        }],
        "steps": steps,
        "style_loras": [],
        "upscale": int(job.get("upscale") or 0),
        "engine": provider,
        "engineSeconds": seconds,
    }


def status() -> dict:
    """What the rail and the dashboard need to know: who can take an order, and the rates."""
    rows = []
    for pid, spec in PROVIDERS.items():
        ok, missing = configured(pid)
        rows.append({
            "id": pid,
            "label": spec.get("label"),
            "kind": spec.get("kind"),
            "configured": ok,
            "missing": missing,
            "rate": rate(pid),
            "perTask": per_task(pid),
            "verified": bool(spec.get("verified")),
            "renderHost": pid in RENDER_HOSTS,
            "capabilities": list(spec.get("capabilities") or ()),
            "note": spec.get("note"),
        })
    return {"providers": rows, "minMarginBps": MIN_MARGIN_BPS,
            "enabled": [r["id"] for r in rows if r["configured"] and r["renderHost"]]}

