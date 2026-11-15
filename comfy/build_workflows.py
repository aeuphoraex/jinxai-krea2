"""Generate the ComfyUI workflows that reproduce this app's pipelines.

Two graphs are written next to this file in ComfyUI UI format (drag onto the canvas):

  orbital_portrait.json   still frames, stacked drift adapters, guidance off
  orbital_motion.json     motion, ascent/descent core pair, one drift adapter on each

Node classes and the sampler contract follow the stock node templates, so the graph loads
in a stock ComfyUI without custom nodes.

Run:  python comfy/build_workflows.py
      python comfy/build_workflows.py --check     # validate what is already on disk
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent

KNOWN = {
    "UNETLoader",
    "CLIPLoader",
    "VAELoader",
    "LoraLoaderModelOnly",
    "CLIPTextEncode",
    "ConditioningZeroOut",
    "EmptyLatentImage",
    "KSampler",
    "KSamplerAdvanced",
    "VAEDecode",
    "VAEEncode",
    "SaveImage",
    "LoadImage",
    "ModelSamplingSD3",
    "WanImageToVideo",
    "SaveWEBM",
    "MarkdownNote",
}

POS_PROMPT = (
    "jinx_film, a navigator in a rust-orange flight suit at a station viewport, "
    "morning light, 35mm film, shallow depth of field"
)
IMAGE_NOTE = """## ORBITAL — portrait array

* **8 steps, CFG 1.0, euler + simple**, 1024x1024 — the distilled core is trained for exactly this.
* **Negative is `ConditioningZeroOut`**, not a text prompt: at CFG 1 the negative branch is unused, which is what `cfg: 0` does in the deck.
* **Drift stack**: chain as many `Lora Loader Model` nodes as needed. Each `strength_model` is that adapter's scale (`style_loras[].scale`).
* **Triggers first**: put every stacked adapter's trigger word at the head of the prompt. The deck re-inserts triggers *after* expansion; stock ComfyUI has no registry, so the text here is the final prompt.
* **Echo branch**: the lower branch is `LoadImage -> VAEEncode -> KSampler (denoise 0.6)`, matching `strength: 0.6`. Unmute it and mute the empty-latent sampler if you only want one path.
* **4K**: the deck re-denoises overlapping 1536 px tiles with RoPE offsets. Stock equivalent is `ImageUpscaleWithModel` / Ultimate SD Upscale at ~0.3 denoise, not wired here."""

WAN_NOTE = """## ORBITAL — motion path

* **Two cores**: ascent model for the first half of the schedule, descent model for the second. `ModelSamplingSD3` shift 5.0 as in the stock template.
* **24 steps split 12/12, CFG 5.0** — the deck defaults (`steps: 24`, `cfg: 5.0`). euler + simple.
* **One adapter per expert**: `Lora Loader Model` after each loader, and the same file for both halves only if the author shipped both. `strength_model` is `lora_scale`.
* **Trigger**: motion adapters fire on a phrase, not a word — put it at the start of the positive prompt. Applies to the same node as the still path.
* **832x480, 81 frames, 24 fps**. `WanImageToVideo.length` must be 4n+1. `SaveWEBM` writes webm; remux with `ffmpeg -i in.webm -c copy out.mp4`.
* **Adapter families must match**: an adapter trained for a different architecture loads but renders garbage here — refuse it by family. ComfyUI will not."""


class Graph:
    """Minimal UI-format workflow builder: ids, slots and links kept consistent."""

    def __init__(self) -> None:
        self.nodes: list[dict] = []
        self.links: list[list] = []
        self._nid = 0
        self._lid = 0

    def add(self, ctype, pos, widgets=(), inputs=(), outputs=(), title=None, size=(320, 120)):
        self._nid += 1
        node = {
            "id": self._nid,
            "type": ctype,
            "pos": list(pos),
            "size": list(size),
            "flags": {},
            "order": self._nid - 1,
            "mode": 0,
            "inputs": [{"name": n, "type": t, "link": None} for n, t in inputs],
            "outputs": [
                {"name": n, "type": t, "links": [], "slot_index": i}
                for i, (n, t) in enumerate(outputs)
            ],
            "properties": {"Node name for S&R": ctype},
            "widgets_values": list(widgets),
        }
        if title:
            node["title"] = title
        self.nodes.append(node)
        return node

    def link(self, src, src_slot, dst, dst_slot):
        out = src["outputs"][src_slot]
        inp = dst["inputs"][dst_slot]
        if out["type"] != inp["type"]:
            raise ValueError(f"type mismatch {out['type']} -> {inp['type']}")
        if inp["link"] is not None:
            raise ValueError(f"{dst['type']}.{inp['name']} connected twice")
        self._lid += 1
        out["links"].append(self._lid)
        inp["link"] = self._lid
        self.links.append([self._lid, src["id"], src_slot, dst["id"], dst_slot, out["type"]])

    def note(self, pos, text, size=(700, 340)):
        return self.add("MarkdownNote", pos, [text], title="BRIEFING", size=size)

    def dump(self, path: pathlib.Path) -> dict:
        data = {
            "last_node_id": self._nid,
            "last_link_id": self._lid,
            "nodes": self.nodes,
            "links": self.links,
            "groups": [],
            "config": {},
            "extra": {"ds": {"scale": 0.8, "offset": [0, 0]}},
            "version": 0.4,
        }
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return data


def image_workflow() -> Graph:
    g = Graph()
    unet = g.add(
        "UNETLoader",
        (0, 0),
        ["krea2_turbo_fp8_scaled.safetensors", "default"],
        outputs=[("MODEL", "MODEL")],
        title="Core · portrait array",
    )
    lora1 = g.add(
        "LoraLoaderModelOnly",
        (380, 0),
        ["None", 0.8],
        inputs=[("model", "MODEL")],
        outputs=[("MODEL", "MODEL")],
        title="Drift 1 · stack member",
    )
    lora2 = g.add(
        "LoraLoaderModelOnly",
        (380, 150),
        ["None", 0.6],
        inputs=[("model", "MODEL")],
        outputs=[("MODEL", "MODEL")],
        title="Drift 2 · stack member",
    )
    clip = g.add(
        "CLIPLoader",
        (0, 250),
        ["qwen3vl_4b_fp8_scaled.safetensors", "krea2", "default"],
        outputs=[("CLIP", "CLIP")],
        title="Text bridge · 12-layer tap",
    )
    vae = g.add(
        "VAELoader",
        (0, 470),
        ["qwen_image_vae.safetensors"],
        outputs=[("VAE", "VAE")],
        title="Latent decoder",
    )
    pos = g.add(
        "CLIPTextEncode",
        (760, 250),
        [POS_PROMPT],
        inputs=[("clip", "CLIP")],
        outputs=[("CONDITIONING", "CONDITIONING")],
        title="Signal · trigger first",
    )
    neg = g.add(
        "ConditioningZeroOut",
        (1120, 470),
        [],
        inputs=[("conditioning", "CONDITIONING")],
        outputs=[("CONDITIONING", "CONDITIONING")],
        title="Void · guidance off",
    )
    latent = g.add(
        "EmptyLatentImage",
        (760, 0),
        [1024, 1024, 1],
        outputs=[("LATENT", "LATENT")],
        title="1024x1024 · still frame",
    )
    ks = g.add(
        "KSampler",
        (1120, 0),
        [5150, "randomize", 8, 1.0, "euler", "simple", 1.0],
        inputs=[
            ("model", "MODEL"),
            ("positive", "CONDITIONING"),
            ("negative", "CONDITIONING"),
            ("latent_image", "LATENT"),
        ],
        outputs=[("LATENT", "LATENT")],
        title="8 steps · CFG 1 · euler/simple",
    )
    dec = g.add(
        "VAEDecode",
        (1500, 0),
        [],
        inputs=[("samples", "LATENT"), ("vae", "VAE")],
        outputs=[("IMAGE", "IMAGE")],
    )
    save = g.add("SaveImage", (1860, 0), ["orbital_portrait"], inputs=[("images", "IMAGE")])

    load = g.add(
        "LoadImage",
        (380, 400),
        ["example.png", "image"],
        outputs=[("IMAGE", "IMAGE")],
        title="Init frame · echo branch (muted)",
    )
    enc = g.add(
        "VAEEncode",
        (760, 400),
        [],
        inputs=[("pixels", "IMAGE"), ("vae", "VAE")],
        outputs=[("LATENT", "LATENT")],
    )
    ks2 = g.add(
        "KSampler",
        (1120, 220),
        [5150, "randomize", 8, 1.0, "euler", "simple", 0.6],
        inputs=[
            ("model", "MODEL"),
            ("positive", "CONDITIONING"),
            ("negative", "CONDITIONING"),
            ("latent_image", "LATENT"),
        ],
        outputs=[("LATENT", "LATENT")],
        title="echo · denoise 0.6",
    )
    dec2 = g.add(
        "VAEDecode",
        (1500, 220),
        [],
        inputs=[("samples", "LATENT"), ("vae", "VAE")],
        outputs=[("IMAGE", "IMAGE")],
    )
    save2 = g.add("SaveImage", (1860, 220), ["orbital_echo"], inputs=[("images", "IMAGE")])

    g.link(unet, 0, lora1, 0)
    g.link(lora1, 0, lora2, 0)
    g.link(clip, 0, pos, 0)
    g.link(pos, 0, neg, 0)
    g.link(lora2, 0, ks, 0)
    g.link(pos, 0, ks, 1)
    g.link(neg, 0, ks, 2)
    g.link(latent, 0, ks, 3)
    g.link(ks, 0, dec, 0)
    g.link(vae, 0, dec, 1)
    g.link(dec, 0, save, 0)

    g.link(load, 0, enc, 0)
    g.link(vae, 0, enc, 1)
    g.link(lora2, 0, ks2, 0)
    g.link(pos, 0, ks2, 1)
    g.link(neg, 0, ks2, 2)
    g.link(enc, 0, ks2, 3)
    g.link(ks2, 0, dec2, 0)
    g.link(vae, 0, dec2, 1)
    g.link(dec2, 0, save2, 0)

    g.note((0, 700), IMAGE_NOTE)
    return g


def video_workflow() -> Graph:
    g = Graph()
    uh = g.add(
        "UNETLoader",
        (0, 0),
        ["wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", "default"],
        outputs=[("MODEL", "MODEL")],
        title="Core · ascent",
    )
    lh = g.add(
        "LoraLoaderModelOnly",
        (380, 0),
        ["None", 1.0],
        inputs=[("model", "MODEL")],
        outputs=[("MODEL", "MODEL")],
        title="Drift · ascent",
    )
    sh = g.add(
        "ModelSamplingSD3",
        (760, 0),
        [5.0],
        inputs=[("model", "MODEL")],
        outputs=[("MODEL", "MODEL")],
        title="shift 5.0",
    )
    ul = g.add(
        "UNETLoader",
        (0, 170),
        ["wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", "default"],
        outputs=[("MODEL", "MODEL")],
        title="Core · descent",
    )
    ll = g.add(
        "LoraLoaderModelOnly",
        (380, 170),
        ["None", 1.0],
        inputs=[("model", "MODEL")],
        outputs=[("MODEL", "MODEL")],
        title="Drift · descent",
    )
    sl = g.add(
        "ModelSamplingSD3",
        (760, 170),
        [5.0],
        inputs=[("model", "MODEL")],
        outputs=[("MODEL", "MODEL")],
        title="shift 5.0",
    )
    clip = g.add(
        "CLIPLoader",
        (0, 350),
        ["umt5_xxl_fp8_e4m3fn_scaled.safetensors", "wan", "default"],
        outputs=[("CLIP", "CLIP")],
        title="Text bridge",
    )
    vae = g.add("VAELoader", (0, 480), ["wan_2.1_vae.safetensors"], outputs=[("VAE", "VAE")])
    load = g.add(
        "LoadImage",
        (0, 620),
        ["example.png", "image"],
        outputs=[("IMAGE", "IMAGE")],
        title="First frame",
    )
    pos = g.add(
        "CLIPTextEncode",
        (380, 350),
        ["a pilot turns toward the viewport as the station rotates, camera slowly pushes in"],
        inputs=[("clip", "CLIP")],
        outputs=[("CONDITIONING", "CONDITIONING")],
        title="Signal · motion phrase first",
    )
    neg = g.add(
        "CLIPTextEncode",
        (380, 480),
        ["色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，"
         "丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，"
         "杂乱的背景，三条腿，背景人很多，倒着走"],
        inputs=[("clip", "CLIP")],
        outputs=[("CONDITIONING", "CONDITIONING")],
        title="Void · stock negative",
    )
    i2v = g.add(
        "WanImageToVideo",
        (1120, 120),
        [832, 480, 81, 1],
        inputs=[
            ("positive", "CONDITIONING"),
            ("negative", "CONDITIONING"),
            ("vae", "VAE"),
            ("clip_vision_output", "CLIP_VISION_OUTPUT"),
            ("start_image", "IMAGE"),
        ],
        outputs=[
            ("positive", "CONDITIONING"),
            ("negative", "CONDITIONING"),
            ("latent", "LATENT"),
        ],
        title="832x480 · 81 frames",
    )
    kh = g.add(
        "KSamplerAdvanced",
        (1500, 0),
        ["enable", 4242, "randomize", 24, 5.0, "euler", "simple", 0, 12, "enable"],
        inputs=[
            ("model", "MODEL"),
            ("positive", "CONDITIONING"),
            ("negative", "CONDITIONING"),
            ("latent_image", "LATENT"),
        ],
        outputs=[("LATENT", "LATENT")],
        title="steps 0-12 · ascent",
    )
    kl = g.add(
        "KSamplerAdvanced",
        (1500, 200),
        ["disable", 4242, "fixed", 24, 5.0, "euler", "simple", 12, 24, "disable"],
        inputs=[
            ("model", "MODEL"),
            ("positive", "CONDITIONING"),
            ("negative", "CONDITIONING"),
            ("latent_image", "LATENT"),
        ],
        outputs=[("LATENT", "LATENT")],
        title="steps 12-24 · descent",
    )
    dec = g.add(
        "VAEDecode",
        (1860, 100),
        [],
        inputs=[("samples", "LATENT"), ("vae", "VAE")],
        outputs=[("IMAGE", "IMAGE")],
    )
    save = g.add(
        "SaveWEBM",
        (2200, 100),
        ["orbital_motion", "vp9", 24, 32],
        inputs=[("images", "IMAGE")],
        title="webm · remux to mp4 with ffmpeg",
    )

    g.link(uh, 0, lh, 0)
    g.link(lh, 0, sh, 0)
    g.link(ul, 0, ll, 0)
    g.link(ll, 0, sl, 0)
    g.link(clip, 0, pos, 0)
    g.link(clip, 0, neg, 0)
    g.link(pos, 0, i2v, 0)
    g.link(neg, 0, i2v, 1)
    g.link(vae, 0, i2v, 2)
    g.link(load, 0, i2v, 4)
    g.link(sh, 0, kh, 0)
    g.link(i2v, 0, kh, 1)
    g.link(i2v, 1, kh, 2)
    g.link(i2v, 2, kh, 3)
    g.link(sl, 0, kl, 0)
    g.link(i2v, 0, kl, 1)
    g.link(i2v, 1, kl, 2)
    g.link(kh, 0, kl, 3)
    g.link(kl, 0, dec, 0)
    g.link(vae, 0, dec, 1)
    g.link(dec, 0, save, 0)

    g.note((0, 820), WAN_NOTE)
    return g


def validate(data: dict, name: str) -> list[str]:
    problems: list[str] = []
    by_id: dict[int, dict] = {}
    for node in data["nodes"]:
        if node["id"] in by_id:
            problems.append(f"{name}: duplicate node id {node['id']}")
        by_id[node["id"]] = node
        if node["type"] not in KNOWN:
            problems.append(f"{name}: node {node['id']} unknown class {node['type']}")

    seen_links: set[int] = set()
    for lid, origin, oslot, target, tslot, ltype in data["links"]:
        if lid in seen_links:
            problems.append(f"{name}: duplicate link {lid}")
        seen_links.add(lid)
        src = by_id.get(origin)
        dst = by_id.get(target)
        if src is None or dst is None:
            problems.append(f"{name}: link {lid} points at a missing node")
            continue
        if oslot >= len(src["outputs"]):
            problems.append(f"{name}: link {lid} origin slot {oslot} out of range on {src['type']}")
            continue
        out = src["outputs"][oslot]
        if out["type"] != ltype:
            problems.append(f"{name}: link {lid} type {ltype} != origin {out['type']}")
        if lid not in out["links"]:
            problems.append(f"{name}: link {lid} missing from {src['type']}({origin}).{out['name']}")
        if tslot >= len(dst["inputs"]):
            problems.append(f"{name}: link {lid} target slot {tslot} out of range on {dst['type']}")
            continue
        inp = dst["inputs"][tslot]
        if inp["type"] != ltype:
            problems.append(f"{name}: link {lid} type {ltype} != input {inp['type']}")
        if inp["link"] != lid:
            problems.append(f"{name}: link {lid} not recorded on {dst['type']}({target}).{inp['name']}")

    for node in data["nodes"]:
        for inp in node["inputs"]:
            if inp["link"] is None and inp["name"] != "clip_vision_output":
                problems.append(f"{name}: {node['type']}({node['id']}).{inp['name']} unconnected")
            if inp["link"] is not None and inp["link"] not in seen_links:
                problems.append(f"{name}: {node['type']}({node['id']}).{inp['name']} stale link")
        for out in node["outputs"]:
            if not out["links"]:
                problems.append(f"{name}: {node['type']}({node['id']}).{out['name']} output unused")
            for lid in out["links"]:
                if lid not in seen_links:
                    problems.append(f"{name}: {node['type']}({node['id']}).{out['name']} stale link {lid}")

    if data["last_node_id"] != max(n["id"] for n in data["nodes"]):
        problems.append(f"{name}: last_node_id does not match the highest id")
    if data["last_link_id"] != max(l[0] for l in data["links"]):
        problems.append(f"{name}: last_link_id does not match the highest id")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="validate the files on disk")
    args = parser.parse_args()

    targets = {
        "orbital_portrait.json": None if args.check else image_workflow(),
        "orbital_motion.json": None if args.check else video_workflow(),
    }

    failed = False
    for filename, graph in targets.items():
        path = HERE / filename
        data = json.loads(path.read_text(encoding="utf-8")) if args.check else graph.dump(path)
        problems = validate(data, filename)
        nodes = [n for n in data["nodes"] if n["type"] != "MarkdownNote"]
        if problems:
            failed = True
            print(f"FAIL {filename}")
            for problem in problems:
                print("  -", problem)
        else:
            print(f"PASS {filename}: {len(nodes)} nodes, {len(data['links'])} links, "
                  f"{sum(1 for n in data['nodes'] if n['type'] == 'MarkdownNote')} notes")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
