"""Offline checks for the RunPod launch artifacts.

No GPU, no keys, no network. The worker is imported with `runpod`, `inference` and `sampling`
replaced by fakes that answer in the documented shapes, so a mistake in the handler surfaces
here instead of on a client's first paid order.

The one check that matters most is the dequant: `krea2.inference._pipeline` loads strict, so an
fp8_scaled checkpoint carrying `<name>_scale` entries fails the job unless it is converted
first. That arithmetic is exercised against fake tensors, exactly as `app.py::_dequant` does it.

    python deploy/runpod/selftest.py
    python deploy/runpod/selftest.py --live --endpoint <id>   # one real job, needs the key
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import types
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))   # repo root, for orbit_guard

FAILS: list[str] = []


def check(name: str, got, want) -> None:
    if got != want:
        FAILS.append(f"{name}: got {got!r}, want {want!r}")
        print(f"  FAIL {name}: {got!r} != {want!r}")
    else:
        print(f"  ok   {name} = {got!r}")


class FakeTensor:
    """Just enough of a tensor for the dequant transform."""

    def __init__(self, value: float, dtype: str = "f32", ndim: int = 2):
        self.value = float(value)
        self.dtype = dtype
        self.ndim = ndim

    def to(self, dtype):
        return FakeTensor(self.value, str(dtype), self.ndim)

    def mul_(self, other):
        self.value *= other.value
        return self

    def float(self):
        return FakeTensor(self.value, "f32", self.ndim)


FAKE_TORCH = types.SimpleNamespace(bfloat16="bf16", float32="f32")


def stubs(width: int, height: int) -> None:
    """Install the modules the handler imports, answering the real shapes."""
    from PIL import Image

    runpod = types.ModuleType("runpod")
    runpod.serverless = types.SimpleNamespace(start=lambda *a, **k: None)
    sys.modules["runpod"] = runpod

    inference = types.ModuleType("inference")
    inference.checkpoints = {"oss_turbo": os.environ.get("OSS_TURBO")}
    calls: list = []

    def _pipeline(checkpoint="oss_turbo"):
        calls.append(checkpoint)
        if not inference.checkpoints.get(checkpoint):
            raise RuntimeError("checkpoint path is empty — OSS_TURBO was not set")
        return ("dit", "ae", "encoder")

    inference._pipeline = _pipeline
    sys.modules["inference"] = inference

    sampling = types.ModuleType("sampling")

    def sample(model, ae, encoder, prompts, **kw):
        return [Image.new("RGB", (int(kw["width"]), int(kw["height"])), (40, 20, 60))]

    sampling.sample = sample
    sys.modules["sampling"] = sampling


POOLS = {"AMPERE_16", "AMPERE_24", "ADA_24", "ADA_32_PRO", "AMPERE_48", "ADA_48_PRO",
         "AMPERE_80", "ADA_80_PRO", "BLACKWELL_96", "HOPPER_141", "BLACKWELL_180"}

# Only the pools this listing advertises; a test pod on hardware we do not offer proves nothing.
POOL_DEVICES = {
    "AMPERE_24": ("NVIDIA L4", "NVIDIA RTX A5000", "NVIDIA GeForce RTX 3090"),
    "ADA_24": ("NVIDIA GeForce RTX 4090",),
    "AMPERE_48": ("NVIDIA A40", "NVIDIA RTX A6000"),
    "ADA_48_PRO": ("NVIDIA L40", "NVIDIA L40S", "NVIDIA RTX 6000 Ada Generation"),
}


def hub_config() -> None:
    """The Hub's two identifier spaces: an endpoint takes pool IDs, a Pod takes device names.

    Mixing them fails at scheduling time with a message about the other file, so it is checked
    offline here rather than discovered in a Hub build log.
    """
    root = os.path.dirname(os.path.dirname(HERE))

    def read(name):
        with open(os.path.join(root, ".runpod", name), encoding="utf-8") as fh:
            return json.load(fh)["config"]

    hub, tests = read("hub.json"), read("tests.json")
    print("the Hub configs name the right kind of GPU")
    wanted = [p.strip() for p in str(hub.get("gpuIds") or "").split(",") if p.strip()]
    check("hub.json gpuIds are pool IDs", [p for p in wanted if p not in POOLS], [])
    device = str(tests.get("gpuTypeId") or "")
    check("tests.json gpuTypeId is not a pool ID", device.upper() in POOLS, False)
    check("tests.json gpuTypeId names a device", device.startswith("NVIDIA "), True)
    check("several CUDA versions are allowed",
          len(tests.get("allowedCudaVersions") or []) >= 3, True)
    check("hub.json agrees on the CUDA list",
          sorted(hub.get("allowedCudaVersions") or []) == sorted(tests.get("allowedCudaVersions") or []),
          True)
    check("both configs ask for one GPU",
          (hub.get("gpuCount"), tests.get("gpuCount")), (1, 1))

    # The test pod must land on hardware the listing actually sells.
    check("test device belongs to an advertised pool",
          [dev for pool, devs in POOL_DEVICES.items() if pool in wanted for dev in devs
           if dev == device], [device])

    # The image's own CUDA floor: `nvidia/cuda:12.8.1-*` declares cuda>=12.8, so any lower
    # entry in allowedCudaVersions only ever schedules onto a host that then fails prestart.
    with open(os.path.join(root, "Dockerfile"), encoding="utf-8") as fh:
        found = re.search(r"FROM\s+nvidia/cuda:(\d+)\.(\d+)", fh.read())
    floor = float(f"{found.group(1)}.{found.group(2)}") if found else 0.0
    allowed = [float(v) for v in (tests.get("allowedCudaVersions") or []) if re.match(r"^\d+\.\d+$", v)]
    check("CUDA list respects the image floor", bool(allowed) and min(allowed) >= floor, True)


def offline() -> int:
    import checkpoint as C

    hub_config()

    print("checkpoint naming")
    check("safetensors -> .bf16", C.target_for("w/krea2_fp8_scaled.safetensors"),
          "w/krea2_fp8_scaled.bf16.safetensors")
    check("already materialized", C.target_for("w/krea2.bf16.safetensors"),
          "w/krea2.bf16.safetensors")
    check("needs dequant on scales",
          C.needs_dequant({"a": 1, "a_scale": 1}), True)
    check("plain state needs none", C.needs_dequant({"a": 1}), False)

    print("\ndequant mirrors app.py::_dequant")
    state = {"blk.weight": FakeTensor(2.0), "blk.weight_scale": FakeTensor(0.5),
             "norm.bias": FakeTensor(3.0, "f32", ndim=1)}
    got = C.dequant(dict(state), FAKE_TORCH)
    check("weight scaled", round(got["blk.weight"].value, 6), 1.0)
    check("scale key removed", "blk.weight_scale" in got, False)
    check("bias untouched", got["norm.bias"].value, 3.0)

    print("\nmaterialize caches and skips work it does not need")
    saved: dict = {}

    def fake_load(path, device="cpu"):
        return {"blk.weight": FakeTensor(4.0), "blk.weight_scale": FakeTensor(0.25)}

    def fake_save(state, path, metadata=None):
        saved["path"] = path
        saved["keys"] = sorted(state)
        saved["weight"] = state["blk.weight"].value

    out = C.materialize("w/krea2_fp8_scaled.safetensors", load=fake_load,
                        save=fake_save, torch_mod=FAKE_TORCH, log=lambda *_: None)
    check("returns the cached name", out, "w/krea2_fp8_scaled.bf16.safetensors")
    check("wrote the cache", saved.get("path"), "w/krea2_fp8_scaled.bf16.safetensors")
    check("no scale key in the output", saved.get("keys"), ["blk.weight"])
    check("dequant value written", round(saved.get("weight") or 0, 6), 1.0)

    plain_saved: dict = {}
    out = C.materialize("w/plain.safetensors", load=lambda p, device="cpu": {"a": FakeTensor(1.0)},
                        save=lambda *a, **k: plain_saved.setdefault("hit", True),
                        torch_mod=FAKE_TORCH, log=lambda *_: None)
    check("plain bf16 used as-is", out, "w/plain.safetensors")
    check("and nothing written", plain_saved.get("hit"), None)
    check("materialized name trusted", C.materialize("w/x.bf16.safetensors"),
          "w/x.bf16.safetensors")

    print("\nthe checkpoint can come from the Hub, not only from a volume")
    check("bare repo id is remote", C.looks_remote("krea/Krea-2-Turbo"), True)
    check("repo id with a filename is remote",
          C.looks_remote("krea/Krea-2-Turbo::turbo.safetensors"), True)
    check("a volume path stays a path",
          C.looks_remote("/runpod-volume/weights/krea2_turbo_fp8_scaled.safetensors"), False)
    check("a bare filename stays a path", C.looks_remote("krea2_turbo_fp8_scaled.safetensors"), False)
    check("split_remote separates repo and file",
          C.split_remote("krea/Krea-2-Turbo::turbo.safetensors"),
          ("krea/Krea-2-Turbo", "turbo.safetensors"))

    seen: dict = {}

    class FakeApi:
        def __init__(self, files=("README.md", "turbo.safetensors")):
            self.files = list(files)

        def list_repo_files(self, repo):
            seen["listed"] = repo
            return self.files

    def fake_download(repo_id, filename, cache_dir=None):
        seen.update(repo=repo_id, file=filename, cache=cache_dir)
        return "/cache/" + filename

    check("fetch picks the only safetensors",
          C.fetch("krea/Krea-2-Turbo", hf=(FakeApi, fake_download), log=lambda *_: None),
          "/cache/turbo.safetensors")
    check("fetch passes repo and file through", (seen.get("repo"), seen.get("file")),
          ("krea/Krea-2-Turbo", "turbo.safetensors"))
    check("fetch honours a :: filename",
          C.fetch("krea/A::b.safetensors", hf=(FakeApi, fake_download), log=lambda *_: None),
          "/cache/b.safetensors")
    try:
        C.fetch("krea/Two", hf=(lambda: FakeApi(("a.safetensors", "b.safetensors")), fake_download),
                log=lambda *_: None)
        ambiguous = "no error"
    except RuntimeError as err:
        ambiguous = "ambiguous" if "name one as" in str(err) else str(err)
    check("fetch refuses to guess between two checkpoints", ambiguous, "ambiguous")

    print("\nthe handler answers in the deployment's shape")
    os.environ["OSS_TURBO"] = os.path.join(HERE, "fake.bf16.safetensors")
    stubs(1280, 1600)
    for name in ("handler", "checkpoint"):
        sys.modules.pop(name, None)
    import handler as H

    out = H.handler({"input": {"prompt": "a woman by a window, 35mm", "width": 1280,
                               "height": 1600, "seed": 11}})
    check("engine is runpod", out.get("engine"), "runpod")
    check("declares the public line", out.get("line"), "public")
    check("steps pinned at 8", out.get("steps"), 8)
    check("one image", len(out.get("images") or []), 1)
    first = (out.get("images") or [{}])[0]
    check("size echoed", (first.get("width"), first.get("height")), (1280, 1600))
    check("seed echoed", first.get("seed"), 11)
    check("data is a png data uri", str(first.get("data"))[:22], "data:image/png;base64,")
    check("decodes to a png", base64.b64decode(first["data"].split(",", 1)[-1])[:8],
          b"\x89PNG\r\n\x1a\n")

    print("\nwhat must be refused before any GPU work")
    check("empty prompt", H.handler({"input": {}})["error"], "prompt is required")
    check("minor refused", H.handler({"input": {"prompt": "a schoolgirl at the gate"}})["error"],
          "refused: school-age framing is not available on this deck")
    check("real person refused",
          ("refused" in str(H.handler({"input": {"prompt": "a photo of a real person"}})["error"])),
          True)
    check("width is clamped and snapped",
          H.handler({"input": {"prompt": "a portrait", "width": 99999}})["images"][0]["width"],
          2048)

    print("\nthe Space points at RunPod, never at the studio deck")
    space = os.path.join(os.path.dirname(HERE), "huggingface", "app.py")
    source = open(space, encoding="utf-8").read()
    check("no studio proxy", 'BASE + "/aigf/api/generate"' in source, False)
    check("no ORBITAL_BASE fallback", "ORBITAL_BASE" in source, False)
    check("calls the RunPod run endpoint", '"/run"' in source, True)
    check("keeps a client-side age floor", "def refuse" in source, True)

    return 0


def live(endpoint: str) -> int:
    """One real job against a deployed endpoint. Costs a few seconds of GPU, nothing else."""
    key = str(os.environ.get("RUNPOD_API_KEY") or "").strip()
    if not key:
        print("RUNPOD_API_KEY is not set")
        return 2
    body = json.dumps({"input": {"prompt": "a portrait, window light, 35mm film",
                                 "width": 1024, "height": 1024, "seed": 7}}).encode()
    req = urllib.request.Request(f"https://api.runpod.ai/v2/{endpoint}/runsync", data=body,
                                 method="POST")
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            got = json.loads(resp.read().decode("utf-8") or "{}")
    except Exception as exc:              # noqa: BLE001 - report, never mask
        print(f"request failed: {type(exc).__name__}: {exc}")
        return 1
    state = str(got.get("status") or "").upper()
    out = got.get("output") or {}
    if state != "COMPLETED" or not isinstance(out, dict):
        print("not completed:", json.dumps(got)[:600])
        return 1
    first = ((out.get("images") or [{}])[0]) or {}
    data = str(first.get("data") or "")
    print(f"status={state} engine={out.get('engine')} line={out.get('line')} "
          f"size={first.get('width')}x{first.get('height')} "
          f"seconds={first.get('seconds')} bytes={len(data)}")
    return 0 if data.startswith("data:image/") else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--endpoint", default=os.environ.get("RUNPOD_ENDPOINT_ID", ""))
    args = parser.parse_args()
    if args.live:
        sys.exit(live(args.endpoint))
    rc = offline()
    if FAILS:
        print(f"\n{len(FAILS)} FAILED")
        sys.exit(1)
    sys.exit(rc)
