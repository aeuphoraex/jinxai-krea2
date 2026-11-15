"""Rewrite a Krea 2 per-image LoRA into the layout third-party loaders expect.

Older files from this app key their tensors as `blocks.N.attn.wq.lora_A.weight`. ComfyUI,
SeaArt and the ai-toolkit-produced LoRAs on Civitai all use the `diffusion_model.` prefix,
and hosted loaders read `ss_network_*` metadata rather than guessing the rank — a bare
file is accepted as a safetensors file and then ignored.

This rewrites only the JSON header (adding the prefix and the loader hints); tensor bytes
are copied untouched, so it is fast and lossless.

    python port_lora.py in.safetensors -o out.safetensors
    python port_lora.py in.safetensors            # writes in.portable.safetensors
"""

from __future__ import annotations

import json
import struct
import sys

PREFIX = "diffusion_model."
CHUNK = 1 << 20


def read_header(path: str) -> tuple[dict, int]:
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        if not 0 < n < 50_000_000:
            raise ValueError(f"{path}: not a safetensors header (length {n})")
        head = json.loads(f.read(n))
        return head, 8 + n


def port(src: str, dst: str) -> tuple[int, int]:
    head, data_start = read_header(src)
    tensors = {k: v for k, v in head.items() if k != "__metadata__"}
    if not tensors:
        raise ValueError(f"{src}: no tensors")
    extent = max(v["data_offsets"][1] for v in tensors.values())

    meta = {str(k): str(v) for k, v in (head.get("__metadata__") or {}).items()}
    meta.setdefault("format", "pt")
    meta.setdefault("ss_network_module", "networks.lora")
    if "rank" in meta:
        meta.setdefault("ss_network_dim", meta["rank"])
    if "alpha" in meta:
        meta.setdefault("ss_network_alpha", meta["alpha"])
    if "ss_base_model_version" not in meta:
        meta["ss_base_model_version"] = "krea2"
    meta.setdefault("modelspec.title", meta.get("prompt", "krea2 image lora")[:60])

    renamed = {}
    for key, v in tensors.items():
        new_key = key if key.startswith(PREFIX) else PREFIX + key
        renamed[new_key] = {
            "dtype": v["dtype"],
            "shape": v["shape"],
            "data_offsets": v["data_offsets"],
        }

    payload = json.dumps(
        {**renamed, "__metadata__": meta}, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    payload += b" " * ((-len(payload)) % 8)

    with open(src, "rb") as f, open(dst, "wb") as g:
        g.write(struct.pack("<Q", len(payload)))
        g.write(payload)
        f.seek(data_start)
        remaining = extent
        while remaining > 0:
            chunk = f.read(min(CHUNK, remaining))
            if not chunk:
                raise IOError(f"{src}: truncated tensor data ({remaining} bytes missing)")
            g.write(chunk)
            remaining -= len(chunk)
    return len(renamed), len(meta)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    src = argv[1]
    dst = argv[argv.index("-o") + 1] if "-o" in argv else src.replace(".safetensors", ".portable.safetensors")
    keys, metas = port(src, dst)
    with open(dst, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        head = json.loads(f.read(n))
    sample = next(k for k in head if k != "__metadata__")
    print(f"wrote {dst}: {keys} tensors, {metas} metadata fields")
    print(f"first key: {sample}")
    print(f"size: {__import__('os').path.getsize(dst) / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
