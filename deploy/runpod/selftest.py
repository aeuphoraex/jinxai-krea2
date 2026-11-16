"""Offline checks for the RunPod launch artifacts.

No GPU, no keys, no network. The worker is imported with `runpod`, `inference` and `sampling`
replaced by fakes that answer in the documented shapes, so a mistake in the handler surfaces
here instead of on a client's first paid order.

The one check that matters most is the dequant: `krea2.inference._pipeline` loads strict, so an
fp8_scaled checkpoint carrying `<name>_scale` entries fails the job unless it is converted
first. That arithmetic is exercised against fake tensors, exactly as `app.py::_dequant` does it.

    python deploy/runpod/selftest.py
    python deploy/runpod/selftest.py --remote                 # checkpoint reachable without a token
    python deploy/runpod/selftest.py --live --endpoint <id>   # one real job, needs the key
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import types
import urllib.error
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


class FakeParam:
    """One parameter: a count and a dtype width are all `tensor_bytes` needs."""

    def __init__(self, count: int = 1, device: str = "cpu"):
        self.count, self.device = count, device
        self.data = self

    def numel(self) -> int:
        return self.count

    def element_size(self) -> int:
        return 2

    def to(self, device):
        self.device, self.data = device, self
        return self


class FakeHandle:
    def __init__(self, bucket: list, fn):
        self.bucket, self.fn = bucket, fn

    def remove(self):
        if self.fn in self.bucket:
            self.bucket.remove(self.fn)


class FakeModule:
    """A module tree with the surface `lowvram` touches, and no torch.

    `place` is where a module's weights are; `at_forward` records where they were when its
    forward actually ran, which is the property the streaming has to get right.
    """

    def __init__(self, *children, params: int = 1):
        self._kids = list(children)
        self._params = [FakeParam() for _ in range(params)]
        self._pre, self._post = [], []
        self.place = "cpu"
        self.at_forward = None
        self.moves = 0

    def children(self):
        return iter(self._kids)

    def modules(self):
        yield self
        for kid in self._kids:
            yield from kid.modules()

    def parameters(self, recurse: bool = True):
        yield from self._params
        if recurse:
            for kid in self._kids:
                yield from kid.parameters()

    def buffers(self, recurse: bool = True):
        return iter(())

    def register_forward_pre_hook(self, fn):
        self._pre.append(fn)
        return FakeHandle(self._pre, fn)

    def register_forward_hook(self, fn):
        self._post.append(fn)
        return FakeHandle(self._post, fn)

    def to(self, device):
        self.place, self.moves = device, self.moves + 1
        for param in self._params:
            param.to(device)
        for kid in self._kids:
            kid.to(device)
        return self

    def __call__(self, value):
        for fn in list(self._pre):
            fn(self, (value,))
        self.at_forward = self.place
        out = self.forward(value)
        for fn in list(self._post):
            fn(self, (value,), out)
        return out

    def forward(self, value):
        return value


class Layer(FakeModule):
    """A decoder layer: its own class, so a stack is homogeneous and its parent is not."""


class Holder(FakeModule):
    """A stack of layers: it calls each layer in order, the way a decoder stack does."""

    def forward(self, value):
        for kid in self._kids:
            value = kid(value)
        return value


class Encoder(FakeModule):
    """The conditioner's outer module: embeddings, then the stack, then the head."""

    def forward(self, value):
        for kid in self._kids:
            value = kid(value)
        return value


def fake_encoder(depth: int = 3, width: int = 5):
    """`(encoder, stack)`, shaped like Qwen3-VL: an embedding, a decoder stack and a head."""
    stack = Holder(*[Layer(params=width) for _ in range(depth)])
    return Encoder(Layer(params=7), stack, Layer(params=1)), stack


def stubs(width: int, height: int) -> dict:
    """Install the modules the handler imports, answering the real shapes.

    Returns call counters, so a check can assert that a refused prompt never reached the GPU.
    """
    from PIL import Image

    runpod = types.ModuleType("runpod")
    runpod.serverless = types.SimpleNamespace(start=lambda *a, **k: None)
    sys.modules["runpod"] = runpod

    inference = types.ModuleType("inference")
    inference.checkpoints = {"oss_turbo": os.environ.get("OSS_TURBO")}
    calls: dict = {"pipeline": [], "sample": []}
    # The handler asks torch how much VRAM is left before deciding whether the encoder may stay on
    # the card. 2 GB free is a 32 GB card with the renderer loaded: the streamed path.
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(mem_get_info=lambda: (2_000_000_000, 34_359_738_368),
                                      empty_cache=lambda: None)
    sys.modules["torch"] = torch
    calls["encoder"], calls["stack"] = fake_encoder()

    def _pipeline(checkpoint="oss_turbo"):
        calls["pipeline"].append(checkpoint)
        if not inference.checkpoints.get(checkpoint):
            raise RuntimeError("checkpoint path is empty — OSS_TURBO was not set")
        return ("dit", "ae", calls["encoder"])

    inference._pipeline = _pipeline
    sys.modules["inference"] = inference

    sampling = types.ModuleType("sampling")

    def sample(model, ae, encoder, prompts, **kw):
        calls["sample"].append({"size": (int(kw["width"]), int(kw["height"])),
                                "text": kw.get("text")})
        return [Image.new("RGB", (int(kw["width"]), int(kw["height"])), (40, 20, 60))]

    sampling.sample = sample
    sys.modules["sampling"] = sampling
    return calls


POOLS = {"AMPERE_16", "AMPERE_24", "ADA_24", "ADA_32_PRO", "AMPERE_48", "ADA_48_PRO",
         "AMPERE_80", "ADA_80_PRO", "BLACKWELL_96", "HOPPER_141", "BLACKWELL_180"}

# VRAM per serverless pool, from RunPod's GPU catalog. An advertised pool is checked against the
# renderer's measured footprint, not against how much spare room a card happens to have.
POOL_VRAM = {"AMPERE_16": 16, "AMPERE_24": 24, "ADA_24": 24, "ADA_32_PRO": 32, "BLACKWELL_32": 32,
             "AMPERE_48": 48, "ADA_48_PRO": 48, "AMPERE_80": 80, "ADA_80_PRO": 80,
             "BLACKWELL_96": 96, "HOPPER_141": 141, "BLACKWELL_180": 180}

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

    # The DiT alone is 26.28 GB, so a 24 GB pool cannot load this worker however the encoder is
    # handled — it dies in `_pipeline` on the first job. `pool_floor_bytes()` is measured from the
    # published files rather than guessed, which is what keeps a 24 GB tier from coming back.
    import lowvram as L
    check("every advertised pool holds the renderer",
          [p for p in wanted if POOL_VRAM.get(p, 0) * 1_000_000_000 < L.pool_floor_bytes()], [])
    device = str(tests.get("gpuTypeId") or "")
    check("tests.json gpuTypeId is not a pool ID", device.upper() in POOLS, False)
    check("tests.json gpuTypeId names a device", device.startswith("NVIDIA "), True)
    check("several CUDA versions are allowed",
          len(tests.get("allowedCudaVersions") or []) >= 3, True)
    check("hub.json agrees on the CUDA list",
          sorted(hub.get("allowedCudaVersions") or []) == sorted(tests.get("allowedCudaVersions") or []),
          True)

    # A third copy lives in publish.py; three lists that disagree is how an endpoint ends up with
    # the list that could not schedule while the Hub has the one that can.
    import publish as P
    check("publish.py agrees on the CUDA list",
          sorted(P.CUDA_VERSIONS) == sorted(tests.get("allowedCudaVersions") or []), True)
    check("both configs ask for one GPU",
          (hub.get("gpuCount"), tests.get("gpuCount")), (1, 1))

    # The test pod must land on hardware the listing actually sells.
    check("test device belongs to an advertised pool",
          [dev for pool, devs in POOL_DEVICES.items() if pool in wanted for dev in devs
           if dev == device], [device])

    # `checkpoint.fetch` refuses to guess between several safetensors files by design, so a
    # remote source has to name one. `krea/Krea-2-Turbo` ships six, and a bare repo id made the
    # first Hub test die at load with "holds several checkpoints".
    import checkpoint as C
    hub_env = {str(e.get("key")): e for e in (hub.get("env") or [])}
    turbo = (((hub_env.get("OSS_TURBO") or {}).get("input") or {}).get("default") or "")
    test_env = {str(e.get("key")): str(e.get("value") or "") for e in (tests.get("env") or [])}
    for label, value in (("tests.json OSS_TURBO", test_env.get("OSS_TURBO") or ""),
                         ("hub.json OSS_TURBO default", str(turbo))):
        check(f"{label} names a file when it is a repo id",
              (not C.looks_remote(value)) or "::" in value, True)
    check("both configs load the same checkpoint",
          str(turbo).split("::")[0] == (test_env.get("OSS_TURBO") or "").split("::")[0], True)

    # The image's own CUDA floor: the `nvidia/cuda:X.Y` base declares `NVIDIA_REQUIRE_CUDA` at
    # that version, so any lower entry in allowedCudaVersions only ever schedules onto a host
    # that then fails container prestart. Read the floor, never hard-code it.
    with open(os.path.join(root, "Dockerfile"), encoding="utf-8") as fh:
        found = re.search(r"FROM\s+nvidia/cuda:(\d+)\.(\d+)", fh.read())
    floor = float(f"{found.group(1)}.{found.group(2)}") if found else 0.0
    allowed = [float(v) for v in (tests.get("allowedCudaVersions") or []) if re.match(r"^\d+\.\d+$", v)]
    check("CUDA list respects the image floor", bool(allowed) and min(allowed) >= floor, True)
    # A list that starts above the floor leaves reachable hosts unused for no reason.
    check("CUDA list reaches the image floor", f"{floor:.1f}" in (tests.get("allowedCudaVersions") or []), True)
    # A list that is only the floor matches nothing on a fleet that has moved on: on 2026-11-16 the
    # A40 pool reported 13.2 alone, so a 12.6-only list is a capacity outage that looks like a sold
    # -out GPU. At least one entry has to be newer than the image.
    check("CUDA list reaches past the floor",
          bool([v for v in allowed if v > floor]), True)


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

    print("\nthe encoder is offloaded instead of co-resident on a 32 GB card")
    import lowvram as L
    check("a card with room keeps the encoder resident", L.strategy(19_000_000_000), "resident")
    check("a 32 GB card with the renderer loaded streams it", L.strategy(3_000_000_000),
          "streamed")
    check("an unreadable device streams it", L.strategy(0), "streamed")
    check("the measured floor fits a 32 GB card", L.pool_floor_bytes() < 32_000_000_000, True)
    check("and does not fit a 24 GB one", L.pool_floor_bytes() > 24_000_000_000, True)

    root, stack = fake_encoder()
    check("the decoder stack is identified by size", L.decoder_layers(root), stack._kids)
    seen: dict = {}

    def encode_run():
        seen["places"] = [kid.place for kid in root._kids]
        return root("prompt")          # a real forward, so the hooks have to fire

    got = L.encode(root, stack._kids, encode_run, device="cuda", park_device="cpu",
                   free=lambda: None, log=lambda *_: None)
    check("encode hands back what the run returned", got, "prompt")
    check("the embedding table is on the card during the encode", seen["places"][0], "cuda")
    check("the stack holder itself never goes up", seen["places"][1], "cpu")
    check("each layer is on the card for its own forward",
          [layer.at_forward for layer in stack._kids], ["cuda"] * 3)
    check("and off it once its forward returns", [layer.place for layer in stack._kids],
          ["cpu"] * 3)
    stack._kids[0]("probe")
    check("no hook survives the encode", stack._kids[0].place, "cpu")
    try:
        L.encode(root, stack._kids, lambda: (_ for _ in ()).throw(RuntimeError("boom")),
                 device="cuda", park_device="cpu", free=lambda *_: None, log=lambda *_: None)
        boom = "no error"
    except RuntimeError:
        boom = "raised"
    check("a failed encode still parks the encoder",
          (boom, [layer.place for layer in stack._kids]), ("raised", ["cpu"] * 3))

    print("\nthe handler answers in the deployment's shape")
    os.environ["OSS_TURBO"] = os.path.join(HERE, "fake.bf16.safetensors")
    calls = stubs(1280, 1600)
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
    # A streamed worker encodes the prompt itself and hands the conditioning over, then parks the
    # stack again because the renderer needs the card.
    check("the sampler got pre-computed conditioning",
          calls["sample"][0].get("text") is not None, True)
    check("the decoder stack is parked for the render",
          [layer.place for layer in calls["stack"]._kids], ["cpu"] * 3)
    check("data is a png data uri", str(first.get("data"))[:22], "data:image/png;base64,")
    check("decodes to a png", base64.b64decode(first["data"].split(",", 1)[-1])[:8],
          b"\x89PNG\r\n\x1a\n")

    # The Hub sends each tests.json `input` as the job's `input`, so that object must be the raw
    # job payload. Nesting it again (`{"input": {"input": {…}}}`) leaves the handler with no
    # `prompt`, which is exactly how a live test pod failed twice on 2026-11-16 with
    # "prompt is required" while every offline check passed.
    print("\nHub test inputs are raw job payloads, and the handler accepts them")
    root = os.path.dirname(os.path.dirname(HERE))
    with open(os.path.join(root, ".runpod", "tests.json"), encoding="utf-8") as fh:
        cases = json.load(fh).get("tests") or []
    check("there are tests", len(cases) >= 1, True)
    for case in cases:
        name = str(case.get("name") or "?")
        payload = case.get("input") or {}
        check(f"{name!r} is not wrapped in a second input", "input" in payload, False)
        check(f"{name!r} carries a prompt", bool(str(payload.get("prompt") or "").strip()), True)
        out = H.handler({"input": payload})
        check(f"{name!r} reaches the workflow", out.get("error"), None)

    print("\nwhat must be refused before any GPU work")
    spent = (len(calls["pipeline"]), len(calls["sample"]))
    check("empty prompt", H.handler({"input": {}}).get("error"), "prompt is required")
    minor = H.handler({"input": {"prompt": "a schoolgirl at the gate"}})
    check("minor refused", minor.get("refused"),
          "school-age framing is not available on this deck")
    # A refusal has to be a *completed* job: RunPod fails any job whose output carries `error`,
    # so a refusal in that shape can never pass the Hub's "refusal costs no GPU time" test.
    check("refusal is not a failed job", "error" in minor, False)
    check("refusal carries no frame", minor.get("images"), [])
    check("refusal costs no GPU time", (len(calls["pipeline"]), len(calls["sample"])), spent)
    check("real person refused",
          "real people" in str(H.handler({"input": {"prompt": "a photo of a real person"}})
                               .get("refused")), True)
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


def remote() -> int:
    """Anonymous reachability of the configured checkpoint, from a fresh worker's point of view.

    A Hub test pod carries no Hugging Face credentials, so the listing's source has to answer an
    anonymous request. `krea/Krea-2-Turbo` is gated ("auto"): the second live test run reached
    the download and died with GatedRepoError 401, which no offline check could see. This asks the
    same question the pod asks, and for a byte rather than the whole 13 GB.
    """
    import checkpoint as C

    root = os.path.dirname(os.path.dirname(HERE))
    with open(os.path.join(root, ".runpod", "hub.json"), encoding="utf-8") as fh:
        hub_env = {str(e.get("key")): e for e in json.load(fh)["config"].get("env") or []}
    with open(os.path.join(root, ".runpod", "tests.json"), encoding="utf-8") as fh:
        test_env = {str(e.get("key")): str(e.get("value") or "")
                    for e in json.load(fh)["config"].get("env") or []}
    sources = (("hub.json default",
                ((hub_env.get("OSS_TURBO") or {}).get("input") or {}).get("default") or ""),
               ("tests.json env", test_env.get("OSS_TURBO") or ""))
    failed = 0
    for label, source in sources:
        repo, filename = C.split_remote(str(source))
        if not C.looks_remote(str(source)) or not filename:
            print(f"  FAIL {label}: not a remote repo id + file: {source!r}")
            failed += 1
            continue
        url = f"https://huggingface.co/{repo}/resolve/main/{filename}"
        try:
            req = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                status = resp.status
                span = str(resp.headers.get("Content-Range") or "")
        except urllib.error.HTTPError as exc:
            print(f"  FAIL {label}: {exc.code} {exc.reason} for {repo}/{filename}")
            print("       a Hub test pod has no HF token; use a source that answers anonymously")
            failed += 1
            continue
        except Exception as exc:              # noqa: BLE001 - report, never mask
            print(f"  FAIL {label}: {type(exc).__name__}: {exc}")
            failed += 1
            continue
        total = span.rsplit("/", 1)[-1] if "/" in span else ""
        gb = int(total) / 1e9 if total.isdigit() else 0.0
        ok = status in (200, 206) and gb > 1
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {status} anonymous, {gb:.1f} GB, {repo}/{filename}")
        failed += 0 if ok else 1
    return 1 if failed else 0


def live(endpoint: str, timeout: int = 1800, interval: int = 20) -> int:
    """One real job against a deployed endpoint, waited out for as long as a cold start takes.

    A cold worker downloads 13.1 GB of checkpoint and dequantises it once before the first frame,
    which is minutes — longer than `/runsync` will hold a connection. So this submits with
    `/run` and polls `/status`, printing progress, because a worker that never scales has to be
    distinguishable from one that is simply loading 26 GB of weights.
    """
    key = str(os.environ.get("RUNPOD_API_KEY") or "").strip()
    if not key:
        print("RUNPOD_API_KEY is not set")
        return 2

    def call(path: str, payload: bytes | None = None, budget: int = 60) -> dict:
        req = urllib.request.Request(f"https://api.runpod.ai/v2/{endpoint}{path}", data=payload,
                                     method="POST" if payload else "GET")
        req.add_header("Authorization", "Bearer " + key)
        if payload:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=budget) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")

    body = json.dumps({"input": {"prompt": "a portrait, window light, 35mm film",
                                 "width": 1024, "height": 1024, "seed": 7}}).encode()
    try:
        job = call("/run", body)
    except Exception as exc:              # noqa: BLE001 - report, never mask
        print(f"submit failed: {type(exc).__name__}: {exc}")
        return 1
    job_id = str(job.get("id") or "")
    if not job_id:
        print("submit carried no id:", json.dumps(job)[:400])
        return 1
    print(f"job {job_id} submitted; waiting up to {timeout}s for a worker")

    started, got, state = time.time(), {}, "IN_QUEUE"
    while True:
        try:
            got = call(f"/status/{job_id}", budget=45)
        except Exception as exc:          # noqa: BLE001 - report, never mask
            print(f"poll failed: {type(exc).__name__}: {exc}")
            return 1
        state = str(got.get("status") or "").upper()
        print(f"  {int(time.time() - started):4d}s {state} delay={got.get('delayTime')} "
              f"exec={got.get('executionTime')} worker={str(got.get('workerId'))[:12]}")
        if state in ("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"):
            break
        if time.time() - started > timeout:
            print(f"gave up after {timeout}s; job {job_id} is still {state}")
            return 1
        time.sleep(interval)

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
    parser.add_argument("--remote", action="store_true",
                        help="check the configured checkpoint downloads without credentials")
    parser.add_argument("--endpoint", default=os.environ.get("RUNPOD_ENDPOINT_ID", ""))
    parser.add_argument("--timeout", type=int, default=1800,
                        help="seconds to wait for a live job, cold start included")
    args = parser.parse_args()
    if args.remote:
        sys.exit(remote())
    if args.live:
        sys.exit(live(args.endpoint, args.timeout))
    rc = offline()
    if FAILS:
        print(f"\n{len(FAILS)} FAILED")
        sys.exit(1)
    sys.exit(rc)
