"""Run the Qwen3-VL conditioner beside the DiT on a card too small for both.

Measured from the published files' own headers on 2026-11-16:

    Krea 2 Turbo DiT (bf16)     26.28 GB    25.0 GB weights + 1.29 GB float32 scalars
    Qwen3-VL-4B-Instruct         8.88 GB    36 decoder layers of 201.9 MB, 0.78 GB embeddings
    Qwen-Image VAE               0.25 GB

`krea2.inference._pipeline` moves all three to the GPU, so the smallest card that could run a
job was a 48 GB one: 34.3 GB of weights before a single activation. That excluded every 24 GB
pool outright — the DiT alone is larger than the card — and every 32 GB pool by a few GB.

Two strategies, chosen from the VRAM left free once the renderer is loaded:

* ``resident`` — the card holds the encoder too (48 GB pools). Nothing moves; this is the old
  behaviour, byte for byte.
* ``streamed`` — park the encoder on the CPU and move one decoder layer at a time through the
  GPU for the length of one prompt encode. The peak becomes the renderer plus the encoder's
  small non-layer modules (``embed_tokens`` 0.78 GB, visual tower 0.83 GB) plus a single
  0.2 GB layer, instead of all 8.88 GB at once. The 7.27 GB layer stack is never on the card
  as a whole, and it is paid for in PCIe traffic (8.88 GB per prompt, ~0.4 s at PCIe 4.0 x16)
  and only while the encoder runs.

Nothing about the render changes: `krea2.sampling.sample` already accepts pre-computed
conditioning (`text=(hiddens, mask)`), so the handler encodes first and hands it over.

Deliberately free of module-level imports — no torch, no transformers — so every rule below is
checkable offline against fake modules.
"""

from __future__ import annotations

# Measured, not estimated: safetensors headers of Comfy-Org/Krea-2, Qwen/Qwen3-VL-4B-Instruct
# and Qwen/Qwen-Image. `--remote` in selftest.py re-checks the DiT's availability.
DIT_BYTES = 26_283_332_608
VAE_BYTES = 253_785_062
ENCODER_BYTES = 8_876_000_000
# Activations are workload-sized, not weight-sized: 4608 tokens at width 6144 in bf16 is ~60 MB
# per hidden state, and the allocator holds freed blocks across a forward. 2 GB is the margin
# that makes a 32 GB card work rather than the measured peak plus nothing.
HEADROOM_BYTES = 2_000_000_000


def pool_floor_bytes() -> int:
    """The smallest card that can load this worker at all, measured from the weights.

    28.5 GB, which is why the listing offers 32 GB pools and up and none of the 24 GB ones.
    """
    return DIT_BYTES + VAE_BYTES + HEADROOM_BYTES


def strategy(free_bytes, encoder_bytes: int = ENCODER_BYTES,
             headroom: int = HEADROOM_BYTES) -> str:
    """``resident`` when the encoder fits beside the renderer, ``streamed`` when it does not.

    Called after the loader has put the DiT and VAE on the card, so the reading it wants is the
    free VRAM *now*, not the card's total. An unreadable device (0) is treated as ``streamed``:
    the safe direction is the one that moves fewer bytes to the GPU.
    """
    return "resident" if int(free_bytes or 0) >= int(encoder_bytes) + int(headroom) else "streamed"


def tensor_bytes(module) -> int:
    """Bytes a module tree occupies on a device."""
    total = 0
    for group in (module.parameters(), module.buffers()):
        for tensor in group:
            try:
                total += int(tensor.numel()) * int(tensor.element_size())
            except AttributeError:          # a fake in the offline checks
                total += int(tensor.numel()) * 2
    return total


def decoder_layers(model, min_children: int = 2) -> list:
    """The decoder stack: the largest run of same-class children in the model.

    Naming it would tie this to one `transformers` release, and the stack is identifiable by
    shape. For Qwen3-VL-4B the text decoder (36 layers, 7.27 GB) beats the visual tower's
    blocks (0.6 GB), which is the one a prompt-only worker wants streamed. An empty list means
    "cannot be streamed safely" and the caller keeps the resident behaviour.
    """
    best, best_bytes = [], 0
    for module in model.modules():
        kids = list(module.children())
        if len(kids) < min_children or len({type(kid) for kid in kids}) != 1:
            continue
        size = sum(tensor_bytes(kid) for kid in kids)
        if size > best_bytes:
            best, best_bytes = kids, size
    return best


def move_outside(model, layers, device) -> int:
    """Put everything that is not a streamed layer on `device`; leave the layers where they are.

    Returns the number of subtrees moved, for the log line. A streamed layer is skipped by
    identity, so walking into its parent does not drag it up — that is the whole point, and the
    guard is what keeps a 26.28 GB renderer from being joined by 7.27 GB of decoder.
    """
    stream_ids = {id(layer) for layer in layers}
    moved = [0]

    def holds_a_layer(module) -> bool:
        return any(id(inner) in stream_ids for inner in module.modules())

    def walk(module) -> None:
        if id(module) in stream_ids:
            return
        kids = list(module.children())
        if not any(holds_a_layer(kid) for kid in kids):
            module.to(device)
            moved[0] += 1
            return
        # A module that owns a layer plus parameters of its own: move the parameters, recurse.
        for param in module.parameters(recurse=False):
            param.data = param.data.to(device)
        for buf in module.buffers(recurse=False):
            buf.data = buf.data.to(device)
        for kid in kids:
            walk(kid)

    walk(model)
    return moved[0]


def _cuda_empty_cache():
    try:
        import torch

        return torch.cuda.empty_cache
    except Exception:                       # noqa: BLE001 - a host with no device needs a no-op
        return lambda: None


def park(model, device: str = "cpu", free=None) -> None:
    """Send the encoder off the compute card and release the blocks it was holding."""
    model.to(device)
    (free or _cuda_empty_cache())()


def encode(encoder, layers, run, device: str = "cuda", park_device: str = "cpu",
           free=None, log=print):
    """Run `run` with the encoder streamed through `device`, then park the encoder again.

    Hooks only exist for the length of the call; `finally` runs them off and parks the encoder
    even when the encode raises, because a worker that keeps half a model on the card between
    jobs has no free VRAM left for the next one. The returned conditioning is untouched by the
    parking — `Module.to` moves parameters, never the tensors a forward produced.
    """
    if not layers:
        return run()

    resident = move_outside(encoder, layers, device)
    streamed = sum(tensor_bytes(layer) for layer in layers)

    def up(module, args):
        module.to(device)

    def down(module, args, output):
        module.to(park_device)

    handles = []
    try:
        for layer in layers:
            handles.append(layer.register_forward_pre_hook(up))
            handles.append(layer.register_forward_hook(down))
        log(f"[lowvram] encoder streamed: {len(layers)} layers, "
            f"{streamed / 1e9:.2f} GB one at a time, {resident} module(s) resident")
        return run()
    finally:
        for handle in handles:
            handle.remove()
        park(encoder, park_device, free)
