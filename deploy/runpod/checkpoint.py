"""Checkpoint preparation for the RunPod worker.

`krea2.inference._pipeline` loads the DiT with `strict=True`. A ComfyUI-style `fp8_scaled`
file carries a per-tensor `<name>_scale` entry beside every weight, so a strict load fails
before the sampler ever runs — the worker would boot, accept a job, and die on the first
render. The Modal worker handles this on load (`app.py::_dequant`); this module is the same
transform for a host that has no `app.py`.

Two rules keep it honest:

* **The transform is identical to `app.py::_dequant`** — bf16 weight times bf16 scale — so a
  frame rendered here and a frame rendered there come from the same arithmetic.
* **The result is cached beside the source**, as `<name>.bf16.safetensors`, so the ~12 GB
  rewrite is paid once per volume rather than once per cold worker.

Pure Python apart from the optional torch/safetensors imports inside `materialize`, which are
injectable so the arithmetic can be checked without a GPU or the real libraries.
"""

from __future__ import annotations

import os
import re

SUFFIX = ".bf16.safetensors"
# A materialized file is ~12 GB; anything smaller is a half-written artifact from a killed
# worker and must be rebuilt rather than loaded.
MIN_BYTES = 1_000_000_000


def needs_dequant(state: dict) -> bool:
    """True when the checkpoint carries ComfyUI per-tensor scales."""
    return any(str(k).endswith("_scale") for k in (state or {}))


def target_for(path: str) -> str:
    """The cached bf16 filename for a source checkpoint."""
    path = str(path or "")
    if path.endswith(SUFFIX):
        return path
    if path.endswith(".safetensors"):
        return path[: -len(".safetensors")] + SUFFIX
    return path + SUFFIX


# --- where the checkpoint comes from -------------------------------------------------------
#
# A worker either finds the DiT on disk (`OSS_TURBO=/runpod-volume/weights/...`) or fetches it
# from the Hub. The second form is what makes a RunPod Hub listing testable: a reviewer's worker
# is built fresh, with no network volume attached, so a path alone fails at load. Two spellings
# are accepted — a bare repo id (`org/name`) and a repo id plus filename
# (`org/name::file.safetensors`). Anything ending in `.safetensors`, absolute, drive-qualified or
# dot-relative stays a filesystem path, so volume deployments and the offline tests are untouched.

_REMOTE_ID = re.compile(r"^[\w.\-]+/[\w.\-]+$")


def split_remote(source: str) -> tuple:
    """`org/name::file.safetensors` -> (repo, filename); filename is "" when not given."""
    repo, _, filename = str(source or "").partition("::")
    return repo.strip(), filename.strip()


def looks_remote(source: str) -> bool:
    """True for a Hugging Face repo id, False for anything meant as a local path."""
    src = str(source or "").strip()
    if not src:
        return False
    if "::" in src:
        return True
    if src.endswith(".safetensors") or "\\" in src:
        return False
    if src.startswith(("/", "~", ".")) or re.match(r"^[A-Za-z]:", src):
        return False
    return bool(_REMOTE_ID.match(src))


def fetch(source: str, cache_dir: str = "", hf=None, log=print) -> str:
    """Download a checkpoint from the Hub and return its local path.

    `hf` is injectable as a `(HfApi, hf_hub_download)` pair so the selection logic can be checked
    without a network. A repo with more than one safetensors file must name the file: guessing
    between them would silently render with the Raw weights instead of Turbo.
    """
    repo, filename = split_remote(source)
    if not repo:
        raise RuntimeError(f"not a Hugging Face repo id: {source!r}")
    if hf is None:
        from huggingface_hub import HfApi, hf_hub_download  # type: ignore

        hf = (HfApi, hf_hub_download)
    api_cls, download = hf
    if not filename:
        candidates = [f for f in api_cls().list_repo_files(repo) if f.endswith(".safetensors")]
        if not candidates:
            raise RuntimeError(f"{repo} publishes no .safetensors file")
        if len(candidates) > 1:
            raise RuntimeError(
                f"{repo} holds several checkpoints ({', '.join(sorted(candidates))}); "
                f"name one as {repo}::<file>.safetensors"
            )
        filename = candidates[0]
    log(f"[checkpoint] fetching {repo}/{filename}")
    return download(
        repo_id=repo,
        filename=filename,
        cache_dir=cache_dir or os.environ.get("HF_HOME") or None,
    )


def dequant(state: dict, torch_mod) -> dict:
    """ComfyUI single-file fp8_scaled (weight + per-tensor scale) -> plain bf16.

    Mirrors `app.py::_dequant` exactly: in place, one pass, no float32 round trip.
    """
    for sk in [k for k in state if str(k).endswith("_scale")]:
        wk = sk[: -len("_scale")]
        weight = state[wk]
        state[wk] = weight.to(torch_mod.bfloat16).mul_(state.pop(sk).to(torch_mod.bfloat16))
        del weight
    return state


def materialize(path: str, load=None, save=None, torch_mod=None, log=print) -> str:
    """Return a loadable bf16 checkpoint, dequantizing once and caching the result.

    `load`, `save` and `torch_mod` exist so the arithmetic is testable offline; production
    calls all three defaults. A checkpoint that is already plain bf16 is returned untouched
    and nothing is written.
    """
    src = str(path or "").strip()
    if not src:
        raise RuntimeError("OSS_TURBO is not set to a checkpoint path or a Hugging Face repo id")
    if looks_remote(src):
        src = fetch(src, log=log)
    if src.endswith(SUFFIX):
        # An already-materialized name is trusted: that is what the volume holds, and
        # re-deriving it would cost a 12 GB rewrite on every cold worker.
        return src
    dst = target_for(src)
    if load is None:
        # Filesystem guards belong to the production path. An injected loader is answering for
        # a volume that is not on this machine, so it must not be pre-empted by a stat().
        if not os.path.exists(src):
            raise RuntimeError(
                f"checkpoint not found: {src}. Put the weights on the network volume and set "
                "OSS_TURBO to the path inside it (see deploy/runpod/README.md)."
            )
        if os.path.exists(dst) and os.path.getsize(dst) >= MIN_BYTES:
            log(f"[checkpoint] reusing materialized {os.path.basename(dst)}")
            return dst
        from safetensors.torch import load_file as load  # type: ignore
    if save is None:
        from safetensors.torch import save_file as save  # type: ignore
    if torch_mod is None:
        import torch as torch_mod  # type: ignore

    state = load(src, device="cpu")
    if not needs_dequant(state):
        log("[checkpoint] already plain bf16, using as-is")
        return src

    log(f"[checkpoint] dequantizing fp8 -> bf16 once: {os.path.basename(src)}")
    state = dequant(state, torch_mod)
    # Same rule as the Modal worker: scalars stay float32 so a norm or a bias is never
    # rounded at bf16 precision.
    for key in list(state):
        value = state[key]
        if getattr(value, "ndim", 2) <= 1 and getattr(value, "dtype", None) != torch_mod.float32:
            state[key] = value.float()
    save(state, dst, metadata={"format": "pt"})
    size = os.path.getsize(dst) if os.path.exists(dst) else 0
    log(f"[checkpoint] wrote {os.path.basename(dst)}"
        + (f" ({size / 1e9:.1f} GB)" if size else ""))
    return dst
